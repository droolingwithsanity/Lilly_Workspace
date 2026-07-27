package com.lilly.assistant.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.ImageButton
import android.widget.TextView
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.appbar.MaterialToolbar
import com.google.android.material.card.MaterialCardView
import com.google.android.material.floatingactionbutton.FloatingActionButton
import com.lilly.assistant.R
import com.lilly.assistant.models.Memory
import kotlinx.coroutines.*

class MemoryActivity : AppCompatActivity() {
    
    private lateinit var memoryAdapter: MemoryAdapter
    private lateinit var recyclerView: RecyclerView
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_memory)
        
        setupViews()
        setupRecyclerView()
        loadMemories()
    }
    
    private fun setupViews() {
        val toolbar = findViewById<MaterialToolbar>(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "Memory"
        
        recyclerView = findViewById(R.id.memoryRecyclerView)
        
        findViewById<FloatingActionButton>(R.id.addMemoryFab).setOnClickListener {
            showAddMemoryDialog()
        }
        
        findViewById<View>(R.id.clearAllButton).setOnClickListener {
            showClearAllDialog()
        }
    }
    
    private fun setupRecyclerView() {
        memoryAdapter = MemoryAdapter(
            onDelete = { memory -> deleteMemory(memory) },
            onEdit = { memory -> showEditMemoryDialog(memory) }
        )
        recyclerView.apply {
            layoutManager = LinearLayoutManager(this@MemoryActivity)
            adapter = memoryAdapter
        }
    }
    
    private fun loadMemories() {
        CoroutineScope(Dispatchers.IO).launch {
            val app = application as? com.lilly.assistant.LillyApplication ?: return@launch
            val memories = app.database.memoryDao().getAllMemories()
            
            withContext(Dispatchers.Main) {
                memoryAdapter.submitList(memories)
            }
        }
    }
    
    private fun deleteMemory(memory: Memory) {
        CoroutineScope(Dispatchers.IO).launch {
            val app = application as? com.lilly.assistant.LillyApplication ?: return@launch
            app.database.memoryDao().deleteMemory(memory)
            
            withContext(Dispatchers.Main) {
                loadMemories()
            }
        }
    }
    
    private fun showAddMemoryDialog() {
        val dialogView = LayoutInflater.from(this).inflate(R.layout.dialog_add_memory, null)
        val keyInput = dialogView.findViewById<com.google.android.material.textfield.TextInputEditText>(R.id.keyInput)
        val valueInput = dialogView.findViewById<com.google.android.material.textfield.TextInputEditText>(R.id.valueInput)
        
        AlertDialog.Builder(this)
            .setTitle("Add Memory")
            .setView(dialogView)
            .setPositiveButton("Add") { _, _ ->
                val key = keyInput.text.toString().trim()
                val value = valueInput.text.toString().trim()
                if (key.isNotEmpty() && value.isNotEmpty()) {
                    addMemory(key, value)
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
    
    private fun showEditMemoryDialog(memory: Memory) {
        val dialogView = LayoutInflater.from(this).inflate(R.layout.dialog_add_memory, null)
        val keyInput = dialogView.findViewById<com.google.android.material.textfield.TextInputEditText>(R.id.keyInput)
        val valueInput = dialogView.findViewById<com.google.android.material.textfield.TextInputEditText>(R.id.valueInput)
        
        keyInput.setText(memory.key)
        valueInput.setText(memory.value)
        
        AlertDialog.Builder(this)
            .setTitle("Edit Memory")
            .setView(dialogView)
            .setPositiveButton("Save") { _, _ ->
                val key = keyInput.text.toString().trim()
                val value = valueInput.text.toString().trim()
                if (key.isNotEmpty() && value.isNotEmpty()) {
                    updateMemory(memory.copy(key = key, value = value))
                }
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
    
    private fun showClearAllDialog() {
        AlertDialog.Builder(this)
            .setTitle("Clear All Memories")
            .setMessage("Are you sure you want to clear all memories? This cannot be undone.")
            .setPositiveButton("Clear All") { _, _ ->
                clearAllMemories()
            }
            .setNegativeButton("Cancel", null)
            .show()
    }
    
    private fun addMemory(key: String, value: String) {
        CoroutineScope(Dispatchers.IO).launch {
            val app = application as? com.lilly.assistant.LillyApplication ?: return@launch
            val memory = Memory(
                id = java.util.UUID.randomUUID().toString(),
                key = key,
                value = value,
                timestamp = System.currentTimeMillis()
            )
            app.database.memoryDao().insertMemory(memory)
            
            withContext(Dispatchers.Main) {
                loadMemories()
            }
        }
    }
    
    private fun updateMemory(memory: Memory) {
        CoroutineScope(Dispatchers.IO).launch {
            val app = application as? com.lilly.assistant.LillyApplication ?: return@launch
            app.database.memoryDao().updateMemory(memory)
            
            withContext(Dispatchers.Main) {
                loadMemories()
            }
        }
    }
    
    private fun clearAllMemories() {
        CoroutineScope(Dispatchers.IO).launch {
            val app = application as? com.lilly.assistant.LillyApplication ?: return@launch
            app.database.memoryDao().clearAllMemories()
            
            withContext(Dispatchers.Main) {
                loadMemories()
            }
        }
    }
    
    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
}

class MemoryAdapter(
    private val onDelete: (Memory) -> Unit,
    private val onEdit: (Memory) -> Unit
) : RecyclerView.Adapter<MemoryAdapter.MemoryViewHolder>() {
    
    private val memories = mutableListOf<Memory>()
    
    fun submitList(newMemories: List<Memory>) {
        memories.clear()
        memories.addAll(newMemories)
        notifyDataSetChanged()
    }
    
    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): MemoryViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_memory, parent, false)
        return MemoryViewHolder(view)
    }
    
    override fun onBindViewHolder(holder: MemoryViewHolder, position: Int) {
        holder.bind(memories[position], onDelete, onEdit)
    }
    
    override fun getItemCount() = memories.size
    
    class MemoryViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val card = itemView.findViewById<MaterialCardView>(R.id.memoryCard)
        private val keyText = itemView.findViewById<TextView>(R.id.memoryKey)
        private val valueText = itemView.findViewById<TextView>(R.id.memoryValue)
        private val timestampText = itemView.findViewById<TextView>(R.id.memoryTimestamp)
        private val deleteButton = itemView.findViewById<ImageButton>(R.id.deleteButton)
        
        fun bind(memory: Memory, onDelete: (Memory) -> Unit, onEdit: (Memory) -> Unit) {
            keyText.text = memory.key
            valueText.text = memory.value
            timestampText.text = java.text.SimpleDateFormat("MMM dd, yyyy HH:mm", java.util.Locale.getDefault())
                .format(java.util.Date(memory.timestamp))
            
            deleteButton.setOnClickListener {
                onDelete(memory)
            }
            
            card.setOnClickListener {
                onEdit(memory)
            }
        }
    }
}