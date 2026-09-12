package com.autoprocedure.plat

import android.content.Intent
import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import android.widget.Toast
import androidx.appcompat.app.AlertDialog
import androidx.fragment.app.Fragment
import androidx.recyclerview.widget.GridLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.autoprocedure.plat.job.JobStore
import com.autoprocedure.plat.job.ProgramCard

class CatalogFragment : Fragment() {
    private lateinit var adapter: ProgramCardAdapter
    private lateinit var emptyText: TextView
    private val sessionListener = AgentSessionListener { snapshot ->
        view?.findViewById<View>(R.id.sync_programs)?.visibility =
            if (snapshot.showProgramSync) View.VISIBLE else View.GONE
    }
    private val onChange: () -> Unit = {
        view?.post { refresh() }
        Unit
    }

    override fun onCreateView(
        inflater: LayoutInflater,
        container: ViewGroup?,
        savedInstanceState: Bundle?,
    ): View {
        return inflater.inflate(R.layout.fragment_catalog, container, false)
    }

    override fun onViewCreated(view: View, savedInstanceState: Bundle?) {
        super.onViewCreated(view, savedInstanceState)
        emptyText = view.findViewById(R.id.empty_text)
        view.findViewById<View>(R.id.sync_programs).setOnClickListener {
            val started = AgentSession.get(requireContext()).syncPrograms()
            Toast.makeText(requireContext(), if (started) R.string.sync_programs_started
                else R.string.sync_programs_offline, Toast.LENGTH_SHORT).show()
        }
        val list = view.findViewById<RecyclerView>(R.id.program_list)
        adapter = ProgramCardAdapter(mutableListOf(), { open(it) }, { confirmDelete(it) })
        list.layoutManager = GridLayoutManager(requireContext(), 2)
        list.adapter = adapter
        JobStore.addListener(onChange)
        AgentSession.get(requireContext()).addListener(sessionListener)
        refresh()
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    override fun onDestroyView() {
        JobStore.removeListener(onChange)
        AgentSession.get(requireContext()).removeListener(sessionListener)
        super.onDestroyView()
    }

    private fun refresh() {
        if (!this::adapter.isInitialized) return
        JobStore.mergeIrPlugins(requireContext())
        val items = JobStore.list()
        adapter.replace(items)
        emptyText.visibility = if (items.isEmpty()) View.VISIBLE else View.GONE
    }

    private fun confirmDelete(card: ProgramCard) {
        AlertDialog.Builder(requireContext())
            .setTitle(R.string.delete_program_title)
            .setMessage(getString(R.string.delete_program_message, card.title.ifBlank { card.taskId }))
            .setNegativeButton(android.R.string.cancel, null)
            .setPositiveButton(R.string.action_delete) { _, _ ->
                JobStore.remove(card)
                Toast.makeText(
                    requireContext(),
                    getString(R.string.delete_program_done, card.title.ifBlank { card.taskId }),
                    Toast.LENGTH_SHORT,
                ).show()
            }
            .show()
    }

    private fun open(card: ProgramCard) {
        if (!card.clickable) return
        if (card.launchType == ProgramCard.LAUNCH_IR) {
            val info = PluginStore(requireContext()).list().firstOrNull { it.programId == card.programId } ?: return
            startActivity(PluginActivity.intent(requireContext(), info.programId, info.versionId))
            return
        }
        startActivity(
            Intent(requireContext(), PluginContainerActivity::class.java)
                .putExtra(PluginContainerActivity.EXTRA_TASK_ID, card.taskId),
        )
    }
}
