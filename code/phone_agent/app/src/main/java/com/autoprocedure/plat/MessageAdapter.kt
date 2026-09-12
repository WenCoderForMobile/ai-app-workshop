package com.autoprocedure.plat

import android.view.Gravity
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.widget.TextView
import androidx.recyclerview.widget.DiffUtil
import androidx.recyclerview.widget.RecyclerView

class MessageAdapter(
    private val items: MutableList<ChatMessage>,
) : RecyclerView.Adapter<MessageAdapter.Holder>() {

    class Holder(view: View) : RecyclerView.ViewHolder(view) {
        val body: TextView = view.findViewById(R.id.message_body)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_message, parent, false)
        return Holder(view)
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val item = items[position]
        holder.body.text = item.text
        val metrics = holder.itemView.resources.displayMetrics
        val list = holder.itemView.parent as? RecyclerView
        val available = if (list != null && list.width > 0) {
            list.width - list.paddingLeft - list.paddingRight
        } else metrics.widthPixels - (24 * metrics.density).toInt()
        holder.body.maxWidth = (available * 0.84f).toInt()
        val params = holder.body.layoutParams as FrameLayout.LayoutParams
        if (item.fromUser) {
            params.gravity = Gravity.END
            holder.body.setBackgroundResource(R.drawable.bg_bubble_user)
        } else {
            params.gravity = Gravity.START
            holder.body.setBackgroundResource(R.drawable.bg_bubble_system)
        }
        holder.body.layoutParams = params
    }

    override fun getItemCount(): Int = items.size

    fun add(message: ChatMessage) {
        items.add(message)
        notifyItemInserted(items.size - 1)
    }

    fun replaceAll(messages: List<ChatMessage>): Boolean {
        if (items == messages) return false
        val previous = items.toList()
        val next = messages.toList()
        val diff = DiffUtil.calculateDiff(object : DiffUtil.Callback() {
            override fun getOldListSize() = previous.size
            override fun getNewListSize() = next.size
            override fun areItemsTheSame(oldPosition: Int, newPosition: Int) =
                previous[oldPosition] == next[newPosition]
            override fun areContentsTheSame(oldPosition: Int, newPosition: Int) =
                previous[oldPosition] == next[newPosition]
        }, false)
        items.clear()
        items.addAll(next)
        diff.dispatchUpdatesTo(this)
        return true
    }
}
