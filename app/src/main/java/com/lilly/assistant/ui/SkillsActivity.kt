package com.lilly.assistant.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.TextView
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.card.MaterialCardView
import com.google.android.material.switchmaterial.SwitchMaterial
import com.lilly.assistant.R
import com.lilly.assistant.models.InstalledSkill
import kotlinx.coroutines.*

class SkillsActivity : AppCompatActivity() {
    
    private lateinit var skillAdapter: SkillAdapter
    private lateinit var recyclerView: RecyclerView
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_skills)
        
        setupViews()
        setupRecyclerView()
        loadSkills()
    }
    
    private fun setupViews() {
        val toolbar = findViewById<MaterialToolbar>(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "Skills"
        
        recyclerView = findViewById(R.id.skillsRecyclerView)
        
        findViewById<View>(R.id.scanButton).setOnClickListener {
            scanForSkills()
        }
    }
    
    private fun setupRecyclerView() {
        skillAdapter = SkillAdapter { skill, enabled ->
            toggleSkill(skill, enabled)
        }
        recyclerView.apply {
            layoutManager = LinearLayoutManager(this@SkillsActivity)
            adapter = skillAdapter
        }
    }
    
    private fun loadSkills() {
        CoroutineScope(Dispatchers.IO).launch {
            val app = application as? com.lilly.assistant.LillyApplication ?: return@launch
            val skills = app.database.skillDao().getAllInstalledSkills()
            
            withContext(Dispatchers.Main) {
                skillAdapter.submitList(skills)
            }
        }
    }
    
    private fun scanForSkills() {
        CoroutineScope(Dispatchers.IO).launch {
            val app = application as? com.lilly.assistant.LillyApplication ?: return@launch
            app.skillManager.scanInstalledSkills()
            
            withContext(Dispatchers.Main) {
                loadSkills()
            }
        }
    }
    
    private fun toggleSkill(skill: InstalledSkill, enabled: Boolean) {
        CoroutineScope(Dispatchers.IO).launch {
            val app = application as? com.lilly.assistant.LillyApplication ?: return@launch
            app.database.skillDao().updateInstalledSkill(skill.copy(isEnabled = enabled))
        }
    }
    
    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
}

class SkillAdapter(
    private val onToggle: (InstalledSkill, Boolean) -> Unit
) : RecyclerView.Adapter<SkillAdapter.SkillViewHolder>() {
    
    private val skills = mutableListOf<InstalledSkill>()
    
    fun submitList(newSkills: List<InstalledSkill>) {
        skills.clear()
        skills.addAll(newSkills)
        notifyDataSetChanged()
    }
    
    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): SkillViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_skill, parent, false)
        return SkillViewHolder(view)
    }
    
    override fun onBindViewHolder(holder: SkillViewHolder, position: Int) {
        holder.bind(skills[position], onToggle)
    }
    
    override fun getItemCount() = skills.size
    
    class SkillViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val card = itemView.findViewById<MaterialCardView>(R.id.skillCard)
        private val nameText = itemView.findViewById<TextView>(R.id.skillName)
        private val packageText = itemView.findViewById<TextView>(R.id.skillPackage)
        private val descriptionText = itemView.findViewById<TextView>(R.id.skillDescription)
        private val enabledSwitch = itemView.findViewById<SwitchMaterial>(R.id.enabledSwitch)
        
        fun bind(skill: InstalledSkill, onToggle: (InstalledSkill, Boolean) -> Unit) {
            nameText.text = skill.name
            packageText.text = skill.packageName
            descriptionText.text = skill.description
            
            enabledSwitch.setOnCheckedChangeListener(null)
            enabledSwitch.isChecked = skill.isEnabled
            enabledSwitch.setOnCheckedChangeListener { _, isChecked ->
                onToggle(skill, isChecked)
            }
            
            card.setOnClickListener {
                // Show skill details
            }
        }
    }
}