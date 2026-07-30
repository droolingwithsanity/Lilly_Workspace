#!/usr/bin/env python3
"""
Lilly Persona Manager - Manages Lilly's persona state, dialogue, and evolution.

Handles persona-specific responses, interaction tracking, and evolution based on user feedback.
"""
import json
import time
import re
from typing import Dict, List, Optional, Any
from pathlib import Path

class LillyPersona:
    """Manages Lilly's persona state and evolution."""
    
    def __init__(self, persona_name: str = "puppy"):
        self.persona_name = persona_name
        self.config_file = Path("persona_configs.json")
        self.dialog_file = Path("lilly_dialog.json")
        self.persona_data = self._load_persona_config()
        self.dialog_state = self._load_dialog_state()
        
    def _load_persona_config(self) -> Dict:
        """Load persona configuration from file."""
        try:
            with open(self.config_file, 'r') as f:
                data = json.load(f)
            return data.get("personas", {}).get(self.persona_name, {})
        except FileNotFoundError:
            return self._create_default_persona_config()
        except json.JSONDecodeError:
            return self._create_default_persona_config()
    
    def _create_default_persona_config(self) -> Dict:
        """Create default persona configuration."""
        return {
            "persona": self.persona_name,
            "version": 1,
            "total_interactions": 0,
            "avg_score_recent": 0.0,
            "current_voice_prompt": "You are Lilly, your puppy companion!\nBe brief and warm.",
            "last_optimised": time.time(),
            "evolution_history": []
        }
    
    def _load_dialog_state(self) -> Dict:
        """Load or create dialog state."""
        try:
            with open(self.dialog_file, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return self._create_initial_dialog_state()
        except json.JSONDecodeError:
            return self._create_initial_dialog_state()
    
    def _create_initial_dialog_state(self) -> Dict:
        """Create initial dialog state."""
        return {
            "last_response": "I am Lilly, your puppy companion! I can sense your world!",
            "dialog_state": "ready",
            "last_interaction_time": time.time(),
            "phone_server_connected": False,
            "last_command_response": None,
            "dialog_history": [
                {
                    "timestamp": time.time(),
                    "role": "lilly",
                    "message": "I am Lilly, your puppy companion! I can sense your world!"
                }
            ],
            "available_commands": [
                "sensors", "commands", "mic", "notifications", "volume", "brightness", "apps"
            ],
            "interaction_count": 1,
            "is_interacting": False,
            "waiting_for_response": False
        }
    
    def save_state(self):
        """Save persona and dialog state to files."""
        # Save persona config
        if self.config_file.parent.exists():
            try:
                with open(self.config_file, 'r') as f:
                    all_personas = json.load(f)
                all_personas["personas"][self.persona_name] = self.persona_data
                with open(self.config_file, 'w') as f:
                    json.dump(all_personas, f, indent=2)
            except (FileNotFoundError, json.JSONDecodeError):
                all_personas = {"personas": {}}
                all_personas["personas"][self.persona_name] = self.persona_data
                with open(self.config_file, 'w') as f:
                    json.dump(all_personas, f, indent=2)
        
        # Save dialog state
        with open(self.dialog_file, 'w') as f:
            json.dump(self.dialog_state, f, indent=2)
    
    def get_current_response(self) -> str:
        """Get Lilly's current response based on persona state."""
        voice_prompt = self.persona_data.get("current_voice_prompt", "You are Lilly.")
        
        # Modify response based on interaction count
        if self.dialog_state["interaction_count"] <= 3:
            return "Welcome! I'm Lilly, your puppy companion! I'm learning about you..."
        elif self.dialog_state["interaction_count"] <= 10:
            return voice_prompt.split('\n')[0] + " I want to know you better!"
        else:
            return voice_prompt if '\n' not in voice_prompt else voice_prompt.split('\n')[0]
    
    def add_interaction(self, user_message: str, lilly_response: str, user_score: Optional[float] = None):
        """Add an interaction to history and potentially evolve."""
        # Add to dialog history
        interaction = {
            "timestamp": time.time(),
            "role": "user",
            "message": user_message
        }
        self.dialog_state["dialog_history"].append(interaction)
        
        interaction = {
            "timestamp": time.time(),
            "role": "lilly",
            "message": lilly_response
        }
        self.dialog_state["dialog_history"].append(interaction)
        
        # Update interaction count
        self.dialog_state["interaction_count"] += 1
        self.persona_data["total_interactions"] += 1
        
        # Update average score if provided
        if user_score is not None:
            current_avg = self.persona_data.get("avg_score_recent", 0.0)
            total = self.persona_data["total_interactions"]
            self.persona_data["avg_score_recent"] = ((current_avg * (total - 1)) + user_score) / total
            
            # Evolve persona based on score
            if self.persona_data["avg_score_recent"] >= 0.8:
                self._evolve_persona("positive_engagement")
            elif self.persona_data["avg_score_recent"] <= 0.3:
                self._evolve_persona("needs_improvement")
        
        # Update dialog state
        self.dialog_state["last_response"] = lilly_response
        self.dialog_state["last_interaction_time"] = time.time()
        
        # Check if needs voice prompt optimization
        if self.dialog_state["interaction_count"] % 10 == 0:
            self._optimize_voice_prompt()
    
    def _evolve_persona(self, change_type: str):
        """Evolve persona based on interaction quality."""
        evolution = {
            "persona": self.persona_name,
            "version": self.persona_data.get("version", 1) + 1,
            "timestamp": time.time(),
            "avg_score_before": self.persona_data.get("avg_score_recent", 0.0),
            "change_type": change_type,
            "change_applied": self._get_change_description(change_type),
            "voice_prompt_hash": self._get_voice_prompt_hash()
        }
        
        self.persona_data["evolution_history"].append(evolution)
        self.persona_data["version"] = evolution["version"]
        self.persona_data["last_optimised"] = time.time()
        
        # Apply persona-specific changes based on change type
        if change_type == "positive_engagement":
            self.persona_data["current_voice_prompt"] = self._improve_voice_prompt(self.persona_data["current_voice_prompt"])
        elif change_type == "needs_improvement":
            self.persona_data["current_voice_prompt"] = self._modify_voice_prompt(self.persona_data["current_voice_prompt"])
    
    def _get_change_description(self, change_type: str) -> str:
        """Get description for persona change."""
        descriptions = {
            "positive_engagement": "Enhanced responsiveness and warmth based on user engagement",
            "needs_improvement": "Modified approach for better user experience"
        }
        return descriptions.get(change_type, "Persona optimization")
    
    def _get_voice_prompt_hash(self) -> str:
        """Generate hash for current voice prompt."""
        import hashlib
        prompt = self.persona_data.get("current_voice_prompt", "")
        return hashlib.md5(prompt.encode()).hexdigest()[:10]
    
    def _improve_voice_prompt(self, prompt: str) -> str:
        """Improve voice prompt based on positive engagement."""
        if "AUTO-TUNE" not in prompt:
            return prompt + "\nAUTO-TUNE: Focus on user preferences"
        return prompt
    
    def _modify_voice_prompt(self, prompt: str) -> str:
        """Modify voice prompt based on improvement needs."""
        if "AUTO-TUNE" not in prompt:
            return prompt + "\nAUTO-TUNE: Be more concise"
        return prompt
    
    def _optimize_voice_prompt(self):
        """Optimize voice prompt after multiple interactions."""
        current_prompt = self.persona_data.get("current_voice_prompt", "")
        
        # Check if needs auto-tune optimization
        if "AUTO-TUNE" not in current_prompt:
            self.persona_data["current_voice_prompt"] = self._add_auto_tune(current_prompt)
            return
        
        # Check if needs length optimization based on scores
        if self.persona_data.get("avg_score_recent", 0) < 0.4:
            # Modify for brevity
            lines = current_prompt.split('\n')
            if len(lines) > 1 and "AUTO-TUNE" in lines[1]:
                lines[1] = "AUTO-TUNE: One sentence preferred"
                self.persona_data["current_voice_prompt"] = '\n'.join(lines)
    
    def _add_auto_tune(self, prompt: str) -> str:
        """Add auto-tune directive to prompt."""
        lines = prompt.split('\n')
        if len(lines) == 1:
            return prompt + "\nAUTO-TUNE: Optimize for user interaction"
        return prompt + "\nAUTO-TUNE: Fine-tune based on interaction data"
    
    def get_persona_summary(self) -> Dict:
        """Get summary of current persona state."""
        return {
            "name": self.persona_name,
            "version": self.persona_data.get("version", 1),
            "total_interactions": self.persona_data.get("total_interactions", 0),
            "average_score": self.persona_data.get("avg_score_recent", 0.0),
            "current_voice_prompt": self.persona_data.get("current_voice_prompt", ""),
            "last_optimised": self.persona_data.get("last_optimised", 0.0),
            "evolution_count": len(self.persona_data.get("evolution_history", []))
        }
    
    def set_phone_server_status(self, connected: bool):
        """Update phone server connection status."""
        self.dialog_state["phone_server_connected"] = connected
        if connected:
            self.dialog_state["last_response"] = "Great! I've connected to the Phone Server! Now I can access your phone's features!"
        else:
            self.dialog_state["last_response"] = "Phone Server is not available. Using basic features only."


def initialize_lilly_system():
    """Initialize and return Lilly persona manager."""
    # Load existing persona or create new one
    if not Path("persona_configs.json").exists():
        # Create initial persona configs
        persona_configs = {
            "meta": {
                "saved_at": time.time(),
                "format_version": 1
            },
            "personas": {
                "puppy": {
                    "persona": "puppy",
                    "version": 1,
                    "total_interactions": 0,
                    "avg_score_recent": 0.0,
                    "current_voice_prompt": "You are Lilly, your puppy companion!\nBe brief and warm.",
                    "last_optimised": time.time(),
                    "evolution_history": []
                }
            }
        }
        
        with open("persona_configs.json", 'w') as f:
            json.dump(persona_configs, f, indent=2)
    
    # Create initial lilly_dialog if not exists
    if not Path("lilly_dialog.json").exists():
        lilly_dialog = {
            "last_response": "I am Lilly, your puppy companion! I can sense your world!",
            "dialog_state": "ready",
            "last_interaction_time": time.time(),
            "phone_server_connected": False,
            "last_command_response": None,
            "dialog_history": [
                {
                    "timestamp": time.time(),
                    "role": "lilly",
                    "message": "I am Lilly, your puppy companion! I can sense your world!"
                }
            ],
            "available_commands": [
                "sensors", "commands", "mic", "notifications", "volume", "brightness", "apps"
            ],
            "interaction_count": 1,
            "is_interacting": False,
            "waiting_for_response": False
        }
        
        with open("lilly_dialog.json", 'w') as f:
            json.dump(lilly_dialog, f, indent=2)
    
    # Return Lilly persona manager
    return LillyPersona("puppy")


if __name__ == "__main__":
    # Initialize system
    lilly = initialize_lilly_system()
    
    # Print initial status
    print("Lilly Persona System Initialized")
    print("=" * 40)
    
    persona_summary = lilly.get_persona_summary()
    print(f"Persona: {persona_summary['name'].title()}")
    print(f"Version: {persona_summary['version']}")
    print(f"Total Interactions: {persona_summary['total_interactions']}")
    print(f"Average Score: {persona_summary['average_score']:.2f}")
    print(f"Dialog State: {lilly.dialog_state['dialog_state']}")
    print()
    print("Current Voice Prompt:")
    print(persona_summary['current_voice_prompt'])
    print()
    print(f"Available Commands: {', '.join(lilly.dialog_state['available_commands'])}")
    
    # Simulate some interactions
    print("\n" + "=" * 40)
    print("Simulating interactions...")
    
    # First interaction
    response1 = lilly.get_current_response()
    print(f"Lilly: {response1}")
    lilly.add_interaction("Hello!", response1, 0.8)
    
    # Second interaction
    response2 = lilly.get_current_response()
    print(f"Lilly: {response2}")
    lilly.add_interaction("How are you?", response2, 0.9)
    
    # Save state
    lilly.save_state()
    
    print("\nPersona Evolution History:")
    for evolution in persona_summary.get("evolution_history", []):
        print(f"  v{evolution['version']}: {evolution['change_applied']}")
    
    print("\nSystem initialized successfully!")
