package com.lilly.assistant.models

import androidx.room.*

@Entity(tableName = "conversations")
data class Conversation(
    @PrimaryKey
    val id: String,
    val messages: List<Message>,
    val createdAt: Long,
    val updatedAt: Long
)

data class Message(
    val id: String,
    val content: String,
    val isUser: Boolean,
    val timestamp: Long
)