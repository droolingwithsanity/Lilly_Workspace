package com.lilly.assistant.ui

import android.os.Bundle
import android.view.LayoutInflater
import android.view.View
import android.view.ViewGroup
import android.widget.EditText
import android.widget.ImageButton
import androidx.appcompat.app.AppCompatActivity
import androidx.recyclerview.widget.LinearLayoutManager
import androidx.recyclerview.widget.RecyclerView
import com.google.android.material.appbar.MaterialToolbar
import com.lilly.assistant.R
import com.lilly.assistant.models.Conversation
import com.lilly.assistant.models.Message
import kotlinx.coroutines.*

class ConversationActivity : AppCompatActivity() {
    
    private lateinit var messageAdapter: MessageAdapter
    private lateinit var recyclerView: RecyclerView
    private lateinit var messageInput: EditText
    private lateinit var sendButton: ImageButton
    private var conversationId: String = ""
    
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_conversation)
        
        conversationId = intent.getStringExtra(EXTRA_CONVERSATION_ID) ?: ""
        
        setupViews()
        setupRecyclerView()
        loadConversation()
    }
    
    private fun setupViews() {
        val toolbar = findViewById<MaterialToolbar>(R.id.toolbar)
        setSupportActionBar(toolbar)
        supportActionBar?.setDisplayHomeAsUpEnabled(true)
        supportActionBar?.title = "Conversation"
        
        recyclerView = findViewById(R.id.messagesRecyclerView)
        messageInput = findViewById(R.id.messageInput)
        sendButton = findViewById(R.id.sendButton)
        
        sendButton.setOnClickListener {
            sendMessage()
        }
    }
    
    private fun setupRecyclerView() {
        messageAdapter = MessageAdapter()
        recyclerView.apply {
            layoutManager = LinearLayoutManager(this@ConversationActivity)
            adapter = messageAdapter
        }
    }
    
    private fun loadConversation() {
        CoroutineScope(Dispatchers.IO).launch {
            val app = application as? com.lilly.assistant.LillyApplication ?: return@launch
            val conversation = app.database.conversationDao().getConversationById(conversationId)
            
            withContext(Dispatchers.Main) {
                conversation?.let {
                    messageAdapter.submitList(it.messages)
                    scrollToBottom()
                }
            }
        }
    }
    
    private fun sendMessage() {
        val text = messageInput.text.toString().trim()
        if (text.isEmpty()) return
        
        messageInput.text.clear()
        
        // Add user message
        val userMessage = Message(
            id = java.util.UUID.randomUUID().toString(),
            content = text,
            isUser = true,
            timestamp = System.currentTimeMillis()
        )
        messageAdapter.addMessage(userMessage)
        scrollToBottom()
        
        // Process with Lilly
        CoroutineScope(Dispatchers.IO).launch {
            val app = application as? com.lilly.assistant.LillyApplication ?: return@launch
            val response = app.core.processInput(com.lilly.assistant.models.UserInput.Text(text))
            
            withContext(Dispatchers.Main) {
                val lillyMessage = Message(
                    id = java.util.UUID.randomUUID().toString(),
                    content = (response as? com.lilly.assistant.models.LillyResponse.Success)?.text ?: "Error processing request",
                    isUser = false,
                    timestamp = System.currentTimeMillis()
                )
                messageAdapter.addMessage(lillyMessage)
                scrollToBottom()
            }
        }
    }
    
    private fun scrollToBottom() {
        if (messageAdapter.itemCount > 0) {
            recyclerView.smoothScrollToPosition(messageAdapter.itemCount - 1)
        }
    }
    
    override fun onSupportNavigateUp(): Boolean {
        finish()
        return true
    }
    
    companion object {
        const val EXTRA_CONVERSATION_ID = "conversation_id"
    }
}

class MessageAdapter : RecyclerView.Adapter<MessageAdapter.MessageViewHolder>() {
    
    private val messages = mutableListOf<Message>()
    
    fun submitList(newMessages: List<Message>) {
        messages.clear()
        messages.addAll(newMessages)
        notifyDataSetChanged()
    }
    
    fun addMessage(message: Message) {
        messages.add(message)
        notifyItemInserted(messages.size - 1)
    }
    
    override fun onCreateViewHolder(parent: ViewGroup, viewType: Int): MessageViewHolder {
        val view = LayoutInflater.from(parent.context)
            .inflate(R.layout.item_message, parent, false)
        return MessageViewHolder(view)
    }
    
    override fun onBindViewHolder(holder: MessageViewHolder, position: Int) {
        holder.bind(messages[position])
    }
    
    override fun getItemCount() = messages.size
    
    class MessageViewHolder(itemView: View) : RecyclerView.ViewHolder(itemView) {
        private val messageText = itemView.findViewById<android.widget.TextView>(R.id.messageText)
        private val timestampText = itemView.findViewById<android.widget.TextView>(R.id.timestampText)
        
        fun bind(message: Message) {
            messageText.text = message.content
            timestampText.text = java.text.SimpleDateFormat("HH:mm", java.util.Locale.getDefault())
                .format(java.util.Date(message.timestamp))
            
            // Set background based on message type
            if (message.isUser) {
                messageText.setBackgroundResource(R.drawable.background_user_message)
                messageText.textAlignment = View.TEXT_ALIGNMENT_VIEW_END
            } else {
                messageText.setBackgroundResource(R.drawable.background_lilly_message)
                messageText.textAlignment = View.TEXT_ALIGNMENT_VIEW_START
            }
        }
    }
}