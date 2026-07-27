package com.lilly.assistant.ui

import android.os.Bundle
import android.widget.EditText
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.button.MaterialButton
import com.lilly.assistant.R

class SystemPromptActivity : AppCompatActivity() {
    
    private lateinit var systemPromptInput: EditText
    private lateinit var customInstructionsInput: EditText
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_system_prompt)
        
        setupViews()
        loadSystemPrompt()
    }
    
    private fun setupViews() {
        val toolbar = findViewById<MaterialToolbar>(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "System Prompt"
        
        systemPromptInput = findViewById(R.id.systemPromptInput)
        customInstructionsInput = findViewById(R.id.customInstructionsInput)
        
        findViewById<MaterialButton>(R.id.saveButton).setOnClickListener {
            saveSystemPrompt()
        }
        
        findViewById<MaterialButton>(R.id.resetButton).setOnClickListener {
            resetToDefault()
        }
    }
    
    private fun loadSystemPrompt() {
        val app = application as? com.lilly.assistant.LillyApplication ?: return
        
        val currentPrompt = app.core.systemPromptManager.getSystemPrompt()
        systemPromptInput.setText(currentPrompt)
        
        val customInstructions = app.core.systemPromptManager.getCustomInstructions()
        customInstructionsInput.setText(customInstructions)
    }
    
    private fun saveSystemPrompt() {
        val app = application as? com.lilly.assistant.LillyApplication ?: return
        
        val prompt = systemPromptInput.text.toString().trim()
        val instructions = customInstructionsInput.text.toString().trim()
        
        app.core.systemPromptManager.setSystemPrompt(prompt)
        app.core.systemPromptManager.setCustomInstructions(instructions)
        
        Toast.makeText(this, "System prompt saved", Toast.LENGTH_SHORT).show()
        finish()
    }
    
    private fun resetToDefault() {
        val app = application as? com.lilly.assistant.LillyApplication ?: return
        
        app.core.systemPromptManager.resetToDefault()
        loadSystemPrompt()
        
        Toast.makeText(this, "Reset to default", Toast.LENGTH_SHORT).show()
    }
    
    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
}