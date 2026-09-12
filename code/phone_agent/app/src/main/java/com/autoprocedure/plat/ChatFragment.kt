package com.autoprocedure.plat

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.EditText
import android.widget.ImageButton
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.core.content.ContextCompat
import androidx.core.widget.doAfterTextChanged
import androidx.fragment.app.Fragment
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.autoprocedure.connect.connection.ConnectionState
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Chat presentation only; the process-scoped session owns connection and jobs. */
class ChatFragment : Fragment() {
    private lateinit var statusText: TextView
    private lateinit var connectionChip: TextView
    private lateinit var inputText: EditText
    private lateinit var voiceButton: ImageButton
    private lateinit var voiceWave: VoiceWaveView
    private lateinit var sendButton: Button
    private lateinit var newMessagesButton: Button
    private lateinit var messageList: RecyclerView
    private lateinit var adapter: MessageAdapter
    private lateinit var asr: PushToTalkAsr
    private lateinit var agentSession: AgentSession
    private var asrWork: Job? = null
    private var busy = false
    private var voiceStatus: String? = null
    private var connectionDetail: String? = null
    private var followNextMessage = false

    private val micPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (view != null) {
            voiceStatus = if (granted) null else getString(R.string.status_mic_denied)
            renderStatus()
        }
    }
    private val sessionListener = AgentSessionListener { snapshot ->
        val follow = adapter.itemCount == 0 || followNextMessage || !messageList.canScrollVertically(1)
        val changed = adapter.replaceAll(snapshot.messages)
        if (changed) {
            if (follow) scrollToLatest() else newMessagesButton.visibility = View.VISIBLE
            followNextMessage = false
        }
        connectionDetail = snapshot.connectionDetail
        renderStatus()
        updateActionButtons()
    }

    override fun onCreateView(inflater: LayoutInflater, container: ViewGroup?, savedInstanceState: Bundle?): View =
        inflater.inflate(R.layout.fragment_chat, container, false)

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        statusText = view.findViewById(R.id.status_text)
        connectionChip = view.findViewById(R.id.connection_chip)
        inputText = view.findViewById(R.id.input_text)
        voiceButton = view.findViewById(R.id.voice_button)
        voiceWave = view.findViewById(R.id.voice_wave)
        sendButton = view.findViewById(R.id.send_button)
        newMessagesButton = view.findViewById(R.id.new_messages_button)
        messageList = view.findViewById(R.id.message_list)
        adapter = MessageAdapter(mutableListOf())
        messageList.layoutManager = LinearLayoutManager(requireContext())
        messageList.adapter = adapter
        messageList.itemAnimator = null
        messageList.addOnScrollListener(object : RecyclerView.OnScrollListener() {
            override fun onScrolled(recyclerView: RecyclerView, dx: Int, dy: Int) {
                if (dy != 0 && !recyclerView.canScrollVertically(1)) newMessagesButton.visibility = View.GONE
            }
        })
        asr = PushToTalkAsr(requireContext().applicationContext)
        busy = false
        agentSession = AgentSession.get(requireContext())
        agentSession.addListener(sessionListener)
        inputText.doAfterTextChanged { updateActionButtons() }
        voiceButton.setOnClickListener { onVoiceClicked() }
        sendButton.setOnClickListener { onSendClicked() }
        newMessagesButton.setOnClickListener { scrollToLatest() }
        connectionChip.setOnClickListener {
            if (agentSession.isConnected()) {
                AlertDialog.Builder(requireContext())
                    .setTitle(R.string.connection_details)
                    .setMessage(R.string.status_connected)
                    .setNegativeButton(android.R.string.cancel, null)
                    .setPositiveButton(R.string.action_disconnect) { _, _ -> agentSession.disconnect() }
                    .show()
            } else agentSession.connect()
        }
        // Technical failure details stay accessible without filling the conversation header.
        connectionChip.setOnLongClickListener {
            AlertDialog.Builder(requireContext()).setTitle(R.string.connection_details)
                .setMessage(connectionDetail ?: statusText.text)
                .setPositiveButton(android.R.string.ok, null).show()
            true
        }
        loadModel()
        agentSession.ensureConnected()
    }

    override fun onPause() {
        // Switching to the program square or leaving the app must stop microphone capture.
        if (asr.isRecording && !busy) stopVoiceInput(sendAfter = false)
        super.onPause()
    }

    override fun onDestroyView() {
        agentSession.removeListener(sessionListener)
        voiceWave.stop()
        val engine = asr
        val work = asrWork
        // Native init/decoding may still be running when the view is destroyed.
        // Release after it completes, rather than freeing its recognizer mid-operation.
        if (work != null && !work.isCompleted) {
            work.cancel()
            work.invokeOnCompletion { engine.release() }
        } else engine.release()
        messageList.adapter = null
        super.onDestroyView()
    }

    private fun scrollToLatest() {
        if (adapter.itemCount > 0) messageList.scrollToPosition(adapter.itemCount - 1)
        newMessagesButton.visibility = View.GONE
    }

    private fun renderStatus() {
        statusText.text = voiceStatus ?: when (agentSession.connectionState()) {
            ConnectionState.CONNECTING -> getString(R.string.status_connecting)
            ConnectionState.CONNECTED -> getString(R.string.status_ready)
            ConnectionState.FAILED, ConnectionState.DISCONNECTED -> getString(R.string.status_disconnected)
        }
    }

    private fun updateActionButtons() {
        sendButton.isEnabled = agentSession.isConnected() && !busy &&
            (asr.isRecording || inputText.text.toString().isNotBlank())
        voiceButton.isEnabled = asr.isReady && !busy
        voiceButton.alpha = if (voiceButton.isEnabled) 1f else 0.4f
        val state = agentSession.connectionState()
        connectionChip.isEnabled = state != ConnectionState.CONNECTING
        val style = when (state) {
            ConnectionState.CONNECTED -> Triple(R.string.chip_connected, R.drawable.bg_chip_ok, R.color.chip_ok)
            ConnectionState.CONNECTING -> Triple(R.string.chip_connecting, R.drawable.bg_chip_busy, R.color.chip_busy)
            ConnectionState.FAILED -> Triple(R.string.chip_failed, R.drawable.bg_chip_err, R.color.chip_err)
            ConnectionState.DISCONNECTED -> Triple(R.string.chip_disconnected, R.drawable.bg_chip_idle, R.color.chip_idle)
        }
        connectionChip.setText(style.first)
        connectionChip.setBackgroundResource(style.second)
        connectionChip.setTextColor(ContextCompat.getColor(requireContext(), style.third))
        connectionChip.contentDescription = getString(style.first) + "，" +
            getString(if (agentSession.isConnected()) R.string.connection_details else R.string.action_connect)
    }

    private fun loadModel() {
        voiceStatus = getString(R.string.status_loading)
        renderStatus()
        val engine = asr
        asrWork = viewLifecycleOwner.lifecycleScope.launch {
            val ok = withContext(Dispatchers.IO) { runCatching { engine.init() }.getOrDefault(false) }
            voiceStatus = if (ok) null else getString(R.string.status_voice_failed)
            renderStatus()
            updateActionButtons()
        }
    }

    private fun onVoiceClicked() {
        if (!asr.isReady || busy) return
        if (asr.isRecording) {
            stopVoiceInput(sendAfter = false)
            return
        }
        if (ContextCompat.checkSelfPermission(requireContext(), Manifest.permission.RECORD_AUDIO) != PackageManager.PERMISSION_GRANTED) {
            micPermission.launch(Manifest.permission.RECORD_AUDIO)
            return
        }
        if (!runCatching { asr.startRecording() }.getOrDefault(false)) {
            voiceStatus = getString(R.string.status_record_failed)
            renderStatus()
            return
        }
        val keyboard = requireContext().getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        keyboard.hideSoftInputFromWindow(inputText.windowToken, 0)
        inputText.visibility = View.INVISIBLE
        voiceWave.visibility = View.VISIBLE
        voiceWave.start()
        voiceButton.setBackgroundResource(R.drawable.bg_voice_button_recording)
        voiceButton.contentDescription = getString(R.string.action_stop_voice)
        voiceStatus = getString(R.string.status_recording)
        renderStatus()
        updateActionButtons()
    }

    private fun onSendClicked() {
        if (!agentSession.isConnected() || busy) return
        if (asr.isRecording) stopVoiceInput(sendAfter = true)
        else sendTypedText(inputText.text.toString().trim())
    }

    private fun stopVoiceInput(sendAfter: Boolean) {
        busy = true
        voiceWave.stop()
        voiceStatus = getString(R.string.status_transcribing)
        renderStatus()
        updateActionButtons()
        val engine = asr
        asrWork = viewLifecycleOwner.lifecycleScope.launch {
            val result = withContext(Dispatchers.IO) { runCatching { engine.stopAndTranscribe() } }
            busy = false
            voiceWave.visibility = View.GONE
            inputText.visibility = View.VISIBLE
            voiceButton.setBackgroundResource(R.drawable.bg_voice_button)
            voiceButton.contentDescription = getString(R.string.action_voice)
            val text = result.getOrDefault("")
            voiceStatus = when {
                result.isFailure -> getString(R.string.status_voice_failed)
                text.isBlank() -> getString(R.string.status_empty_speech)
                else -> null
            }
            if (text.isNotBlank()) {
                // Preserve an existing typed draft when adding dictated text.
                val draft = inputText.text.toString().trim()
                inputText.setText(if (draft.isBlank()) text else "$draft\n$text")
                inputText.setSelection(inputText.length())
                if (sendAfter) sendTypedText(inputText.text.toString().trim())
            }
            renderStatus()
            updateActionButtons()
        }
    }

    private fun sendTypedText(text: String) {
        if (text.isBlank() || !agentSession.isConnected()) return
        followNextMessage = true
        agentSession.sendChat(text)
        inputText.setText("")
    }
}
