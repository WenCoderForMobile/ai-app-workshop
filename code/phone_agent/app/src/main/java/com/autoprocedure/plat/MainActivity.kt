package com.autoprocedure.plat

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity
import androidx.fragment.app.Fragment
import androidx.viewpager2.adapter.FragmentStateAdapter
import androidx.viewpager2.widget.ViewPager2
import com.google.android.material.tabs.TabLayout
import com.google.android.material.tabs.TabLayoutMediator

class MainActivity : AppCompatActivity() {
    private lateinit var pager: ViewPager2

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)
        title = getString(R.string.app_name)
        AgentSession.get(applicationContext).ensureConnected()

        pager = findViewById(R.id.pager)
        val tabs = findViewById<TabLayout>(R.id.tabs)
        pager.offscreenPageLimit = 1
        pager.adapter = object : FragmentStateAdapter(this) {
            override fun getItemCount(): Int = 2
            override fun createFragment(position: Int): Fragment {
                return if (position == 0) ChatFragment() else CatalogFragment()
            }
        }
        TabLayoutMediator(tabs, pager) { tab, position ->
            tab.setText(if (position == 0) R.string.tab_chat else R.string.tab_programs)
        }.attach()
    }
}
