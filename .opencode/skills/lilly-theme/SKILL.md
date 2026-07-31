---
name: lilly-theme
description: Use when customizing Lilly AI themes, creating popout interfaces, managing glass UI effects, or configuring visual appearance. Front-load keywords like theme, popout, glass, UI, appearance, visual, design, interface.
---

# Lilly Theme & Popout Skill

Use this skill when the user needs to customize the visual appearance of Lilly AI, create popout interfaces, or manage glass UI effects.

## Theme System

Lilly AI supports multiple theme layers:

### 1. Avatar Themes
Each avatar persona has its own visual theme:
- Bear, Bunny, Cat, Deer, Fox, Owl, Puppy, Raccoon, Wolf

### 2. Glass Effects
The overlay app supports glassmorphism effects:
- Blur backgrounds
- Transparency layers
- Shadow effects
- Border radius

### 3. Popout Interfaces
Create floating windows for:
- Chat bubbles
- Status indicators
- Quick actions
- Notification panels

## Configuration Files

### Theme Configuration
Location: `persona_configs.json`

```json
{
  "theme": {
    "primary_color": "#6366f1",
    "secondary_color": "#8b5cf6",
    "background": "rgba(0, 0, 0, 0.8)",
    "glass_blur": "20px",
    "border_radius": "16px"
  }
}
```

### Popout Settings
Location: `lilly-overlay-app/`

The Android overlay app handles:
- Floating windows
- System alerts
- Picture-in-picture mode
- Quick settings tiles

## Creating Custom Themes

### Step 1: Define Color Palette
```json
{
  "colors": {
    "primary": "#HEX",
    "secondary": "#HEX",
    "accent": "#HEX",
    "background": "rgba(R, G, B, A)",
    "text": "#HEX"
  }
}
```

### Step 2: Configure Glass Effects
```json
{
  "glass": {
    "blur": "20px",
    "transparency": 0.8,
    "border": "1px solid rgba(255, 255, 255, 0.2)",
    "shadow": "0 8px 32px rgba(0, 0, 0, 0.37)"
  }
}
```

### Step 3: Set Popout Behavior
```json
{
  "popout": {
    "position": "bottom-right",
    "size": "medium",
    "animation": "slide-in",
    "auto_hide": true
  }
}
```

## Integration Points

### With Termux
- SSH commands to update overlay app settings
- Push theme files to phone
- Restart overlay service

### With Docker
- Serve theme assets via HTTP
- Cache static resources
- Handle theme versioning

### With Lilly Backend
- API endpoints for theme management
- User preference storage
- A/B testing themes

## Quick Actions

### Apply Theme
```bash
# Via SSH to phone
ssh -i $TERMUX_SSH_KEY -p $TERMUX_SSH_PORT $TERMUX_SSH_USER@$TERMUX_SSH_HOST "am broadcast -a com.lilly.UPDATE_THEME --es theme 'dark'"
```

### Toggle Popout
```bash
# Via SSH to phone
ssh -i $TERMUX_SSH_KEY -p $TERMUX_SSH_PORT $TERMUX_SSH_USER@$TERMUX_SSH_HOST "am broadcast -a com.lilly.TOGGLE_POPOUT"
```

### Check Overlay Status
```bash
# Via SSH to phone
ssh -i $TERMUX_SSH_KEY -p $TERMUX_SSH_PORT $TERMUX_SSH_USER@$TERMUX_SSH_HOST "dumpsys activity services com.lilly.overlay"
```

## Design Guidelines

1. **Consistency**: Use the same color palette across all UI elements
2. **Accessibility**: Ensure sufficient contrast for text readability
3. **Performance**: Minimize blur effects on older devices
4. **Battery**: Reduce animations when battery is low
5. **Night Mode**: Support automatic theme switching based on time

## File Structure

```
lilly-overlay-app/
├── src/
│   └── main/
│       ├── java/
│       │   └── com/lilly/overlay/
│       │       ├── OverlayService.java
│       │       ├── ThemeManager.java
│       │       └── PopoutWindow.java
│       └── res/
│           ├── layout/
│           ├── values/
│           └── drawable/
├── build.gradle
└── src/main/AndroidManifest.xml
```

When helping users with themes, always check the current persona configuration and suggest changes that maintain visual consistency across the Lilly AI ecosystem.
