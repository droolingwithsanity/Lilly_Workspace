package com.lilly.assistant.ui

import android.content.Intent
import android.os.Bundle
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.content.Context
import androidx.appcompat.app.AppCompatActivity
import androidx.lifecycle.lifecycleScope
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.lilly.assistant.LillyApplication
import com.lilly.assistant.R
import com.lilly.assistant.models.*
import com.lilly.assistant.services.LillyForegroundService
import kotlinx.coroutines.*

class MainActivity : AppCompatActivity() {

    private lateinit var messageAdapter: MainMessageAdapter
    private val app by lazy { application as LillyApplication }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        setupUI()
        setupListeners()
        startService()
    }

    private fun setupUI() {
        val toolbar = findViewById<com.google.android.material.appbar.MaterialToolbar>(R.id.toolbar)
        setSupportActionBar(toolbar)

        messageAdapter = MainMessageAdapter()
        findViewById<RecyclerView>(R.id.messagesRecyclerView).apply {
            layoutManager = LinearLayoutManager(this@MainActivity)
            adapter = messageAdapter
        }

        loadConversationHistory()
    }

    private fun setupListeners() {
        findViewById<com.google.android.material.floatingactionbutton.FloatingActionButton>(R.id.sendButton).setOnClickListener {
            sendMessage()
        }

        findViewById<android.widget.ImageButton>(R.id.voiceButton).setOnClickListener {
            toggleVoiceInput()
        }

        findViewById<android.widget.ImageButton>(R.id.settingsButton).setOnClickListener {
            openSettings()
        }

        findViewById<android.widget.ImageButton>(R.id.skillsButton).setOnClickListener {
            openSkills()
        }

        findViewById<android.widget.ImageButton>(R.id.clearButton).setOnClickListener {
            clearChat()
        }
    }

    private fun sendMessage() {
        val input = findViewById<com.google.android.material.textfield.TextInputEditText>(R.id.messageInput)
        val message = input.text.toString().trim()
        if (message.isEmpty()) return

        input.text?.clear()
        addMessageToUI(message, isUser = true)
        hideKeyboard()
        processMessage(message)
    }

    private fun processMessage(message: String) {
        lifecycleScope.launch {
            showTypingIndicator()

            val response = app.core.processInput(UserInput.Text(message))

            hideTypingIndicator()

            when (response) {
                is LillyResponse.Success -> {
                    addMessageToUI(response.text, isUser = false)
                }
                is LillyResponse.Error -> {
                    addMessageToUI("Error: ${response.message}", isUser = false)
                }
            }
        }
    }

    private fun addMessageToUI(message: String, isUser: Boolean) {
        val messageModel = MainMessageModel(
            text = message,
            isUser = isUser,
            timestamp = System.currentTimeMillis()
        )
        messageAdapter.addMessage(messageModel)
        findViewById<RecyclerView>(R.id.messagesRecyclerView).scrollToPosition(messageAdapter.itemCount - 1)
    }

    private fun showTypingIndicator() {
        findViewById<View>(R.id.typingIndicator).visibility = View.VISIBLE
    }

    private fun hideTypingIndicator() {
        findViewById<View>(R.id.typingIndicator).visibility = View.GONE
    }

    private fun toggleVoiceInput() {
        // TODO: Integrate with Android speech recognition
    }

    private fun openSettings() {
        startActivity(Intent(this, SettingsActivity::class.java))
    }

    private fun openSkills() {
        startActivity(Intent(this, SkillsActivity::class.java))
    }

    private fun clearChat() {
        messageAdapter.clearMessages()
    }

    private fun loadConversationHistory() {
        lifecycleScope.launch {
            val conversations = app.core.getAllConversations()
            val latestConversation = conversations.firstOrNull()
            latestConversation?.messages?.forEach { message ->
                addMessageToUI(message.content, isUser = message.isUser)
            }
        }
    }

    private fun hideKeyboard() {
        val imm = getSystemService(Context.INPUT_METHOD_SERVICE) as InputMethodManager
        val input = findViewById<com.google.android.material.textfield.TextInputEditText>(R.id.messageInput)
        imm.hideSoftInputFromWindow(input.windowToken, 0)
    }

    private fun startService() {
        LillyForegroundService.startService(this)
    }

    override fun onDestroy() {
        super.onDestroy()
        app.core.shutdown()
    }
}

class MainMessageAdapter : RecyclerView.Adapter<MainMessageAdapter.MessageViewHolder>() {

    private val messages = mutableListOf<MainMessageModel>()

    class MessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        val messageText: android.widget.TextView = itemView.findViewById(R.id.messageText)
        val timestampText: android.widget.TextView = itemView.findViewById(R.id.timestampText)
    }

    override fun onCreateViewHolder(parent: android.view.ViewGroup, viewType: Int): MessageViewHolder {
        val view = android.view.LayoutInflater.from(parent.context)
            .inflate(R.layout.item_message, parent, false)
        return MessageViewHolder(view)
    }

    override fun onBindViewHolder(holder: MessageViewHolder, position: Int) {
        val message = messages[position]
        holder.messageText.text = message.text
        holder.timestampText.text = java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault())
            .format(java.util.Date(message.timestamp))

        if (message.isUser) {
            holder.itemView.findViewById<View>(R.id.messageText).setBackgroundResource(R.drawable.background_user_message)
            holder.messageText.textAlignment = View.TEXT_ALIGNMENT_VIEW_END
        } else {
            holder.itemView.findViewById<View>(R.id.messageText).setBackgroundResource(R.drawable.background_lilly_message)
            holder.messageText.textAlignment = View.TEXT_ALIGNMENT_VIEW_START
        }
    }

    override fun getItemCount(): Int = messages.size

    fun addMessage(message: MainMessageModel) {
        messages.add(message)
        notifyItemInserted(messages.size - 1)
    }

    fun clearMessages() {
        messages.clear()
        notifyDataSetChanged()
    }
}

data class MainMessageModel(
    val text: String,
    val isUser: Boolean,
    val timestamp: Long
)