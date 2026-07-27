package com.lilly.assistant.models

import androidx.room.*

@Entity(tableName = "memories")
data class Memory(
    @PrimaryKey
    val id: String,
    val key: String,
    val value: String,
    val timestamp: Long
)