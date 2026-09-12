package com.autoprocedure.plat

import android.graphics.Color
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.recyclerview.widget.RecyclerView
import com.autoprocedure.plat.job.ProgramCard

class ProgramCardAdapter(
    private val items: MutableList<ProgramCard>,
    private val onClick: (ProgramCard) -> Unit,
    private val onDelete: (ProgramCard) -> Unit,
) : RecyclerView.Adapter<ProgramCardAdapter.Holder>() {

    class Holder(view: View) : RecyclerView.ViewHolder(view) {
        val body: View = view.findViewById(R.id.program_body)
        val icon: TextView = view.findViewById(R.id.program_icon)
        val title: TextView = view.findViewById(R.id.program_title)
        val summary: TextView = view.findViewById(R.id.program_summary)
        val state: TextView = view.findViewById(R.id.program_state)
        val delete: TextView = view.findViewById(R.id.program_delete)
    }

    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): Holder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_program_card, parent, false)
        return Holder(view)
    }

    override fun onBindViewHolder(holder: Holder, position: Int) {
        val item = items[position]
        holder.icon.text = item.icon.ifBlank { "📦" }
        holder.title.text = item.title
        holder.summary.text = item.summary.ifBlank { item.packageName.ifBlank { item.programId } }
        holder.state.text = if (item.state == ProgramCard.STATE_FAILED && item.error.isNotBlank())
            "${item.stateLabel}：${item.error.take(120)}" else item.stateLabel
        val ready = item.clickable
        holder.body.alpha = if (ready) 1f else 0.42f
        holder.itemView.isEnabled = true
        holder.itemView.isClickable = true
        holder.title.setTextColor(if (ready) Color.BLACK else Color.parseColor("#FF757575"))
        holder.state.setTextColor(
            if (ready) Color.parseColor("#FF2E7D32") else Color.parseColor("#FF9E9E9E"),
        )
        holder.itemView.setOnClickListener {
            if (item.clickable) onClick(item)
        }
        holder.itemView.setOnLongClickListener {
            onDelete(item)
            true
        }
        holder.delete.setOnClickListener { onDelete(item) }
    }

    override fun getItemCount(): Int = items.size

    fun replace(next: List<ProgramCard>) {
        items.clear()
        items.addAll(next)
        notifyDataSetChanged()
    }
}
