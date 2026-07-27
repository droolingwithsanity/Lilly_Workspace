package com.lilly.assistant.models

import androidx.room.*

@Entity(tableName = "preferences")
data class Preference(
    @PrimaryKey
    val key: String,
    val value: String,
    val updatedAt: Long
)