"""Generate demo screenshots for the DEMO workflow."""
import os
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path("screenshots")
OUT.mkdir(exist_ok=True)

W, H = 360, 760
BG = (248, 249, 250)
WHITE = (255, 255, 255)
CARD = (255, 255, 255)
PINK = (244, 114, 182)
PURPLE = (167, 139, 250)
GREEN = (52, 211, 153)
YELLOW = (251, 191, 36)
TEXT_DARK = (45, 55, 72)
TEXT_MED = (74, 85, 104)
TEXT_LIGHT = (113, 128, 150)
BORDER = (226, 232, 240)
STATUSBAR = (241, 245, 249)

def getfont(size=14):
    try: return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
    except: return ImageFont.load_default()

def status_bar(draw):
    draw.rectangle([(0,0),(W,28)], fill=STATUSBAR)
    draw.text((12,6), "9:41", fill=TEXT_DARK, font=getfont(12))
    draw.text((W-70,6), "🔋 87%", fill=TEXT_LIGHT, font=getfont(10))

def phone_frame(img):
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([(0,0),(W-1,H-1)], radius=20, outline=BORDER, width=2)

def make_step1():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    status_bar(draw)
    draw.text((W//2,60), "Pick your companion", fill=TEXT_DARK, font=getfont(16), anchor="mt")
    avatars = [("[Puppy]","Puppy"),("[Fox]","Fox"),("[Cat]","Cat"),("[Bear]","Bear"),("[Bunny]","Bunny"),
               ("[Owl]","Owl"),("[Deer]","Deer"),("[Wolf]","Wolf"),("[Raccoon]","Raccoon")]
    cx, cy = W//2, 180
    draw.ellipse([(cx-50,cy-50),(cx+50,cy+50)], fill=PINK, outline=PURPLE, width=3)
    draw.text((cx,cy), "🐶", font=getfont(36), anchor="mm")
    draw.text((cx,cy+65), "Lilly the Puppy", fill=TEXT_DARK, font=getfont(13), anchor="mt")
    draw.text((cx,cy+85), "Alpha Companion", fill=TEXT_LIGHT, font=getfont(10), anchor="mt")
    for i, (e, n) in enumerate(avatars):
        x = 40 + (i % 3) * 100
        y = 340 + (i // 3) * 90
        draw.rounded_rectangle([(x-20,y-20),(x+20,y+20)], radius=10, fill=WHITE)
        draw.text((x,y), e, font=getfont(22), anchor="mm")
        draw.text((x,y+30), n, fill=TEXT_LIGHT, font=getfont(8), anchor="mt")
    draw.rounded_rectangle([(40,H-70),(W-40,H-20)], radius=12, fill=PURPLE)
    draw.text((W//2,H-45), "Sign in with Google", fill=WHITE, font=getfont(13), anchor="mm")
    phone_frame(img)
    img.save(OUT / "step1-home.png")
    print("  ✓ step1-home.png")

def make_step2():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    status_bar(draw)
    draw.text((W//2,42), "Chat", fill=TEXT_DARK, font=getfont(16), anchor="mt")
    bubbles = [
        (50, 70, "what do you sense?", 0, "user"),
        (160, 105, "Ooh! Lots happening! It's dim\nat 23 lux — cozy vibes. I feel\nsome gentle motion, like you're\nsitting. Battery's at 87%.\nAnd you just got an email\nfrom Sarah! Want me to\nread it?", 1, "bot"),
    ]
    for bx, by, txt, side, kind in bubbles:
        color = PURPLE if kind == "user" else WHITE
        x = 20 if side == 0 else 40
        draw.rounded_rectangle([(x,by),(x+300,by+25*len(txt.splitlines()))], radius=12, fill=color)
        for j, line in enumerate(txt.splitlines()):
            c = WHITE if kind == "user" else TEXT_DARK
            draw.text((x+12, by+8+j*22), line, fill=c, font=getfont(11))
    draw.rounded_rectangle([(20,H-60),(W-20,H-10)], radius=16, fill=WHITE)
    draw.text((40,H-38), "Type a message...", fill=TEXT_LIGHT, font=getfont(12))
    draw.rounded_rectangle([(W-70,H-52),(W-28,H-18)], radius=10, fill=PURPLE)
    draw.text((W-49,H-35), "Send", fill=WHITE, font=getfont(11), anchor="mm")
    phone_frame(img)
    img.save(OUT / "step2-chat.png")
    print("  ✓ step2-chat.png")

def make_step3():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    status_bar(draw)
    draw.rectangle([(0,28),(W,500)], fill=(226, 232, 240))
    draw.text((W//2,260), "Camera feed", fill=TEXT_LIGHT, font=getfont(18), anchor="mm")
    draw.text((W//2,290), "(viewfinder)", fill=TEXT_LIGHT, font=getfont(12), anchor="mm")
    pip_x, pip_y = 160, 520
    draw.rounded_rectangle([(pip_x,pip_y),(pip_x+180,pip_y+140)], radius=16, fill=WHITE, outline=PURPLE, width=2)
    draw.text((pip_x+90,pip_y+50), "📸", font=getfont(32), anchor="mm")
    draw.text((pip_x+90,pip_y+85), "Camera PiP", fill=TEXT_DARK, font=getfont(9), anchor="mm")
    draw.text((pip_x+90,pip_y+105), "drag to move", fill=TEXT_LIGHT, font=getfont(8), anchor="mm")
    draw.text((20,540), "Say:", fill=TEXT_LIGHT, font=getfont(11))
    draw.rounded_rectangle([(20,565),(200,590)], radius=8, fill=WHITE)
    draw.text((30,575), '"what do you see?"', fill=PINK, font=getfont(10))
    phone_frame(img)
    img.save(OUT / "step3-camera-pip.png")
    print("  ✓ step3-camera-pip.png")

def make_step4():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    status_bar(draw)
    draw.rectangle([(0,28),(W,400)], fill=(226, 232, 240))
    draw.text((W//2,200), "Camera feed", fill=TEXT_LIGHT, font=getfont(16), anchor="mm")
    boxes = [(80,120,"person",0.94),(180,180,"laptop",0.88),(280,250,"cup",0.76)]
    for x,y,lbl,conf in boxes:
        draw.rectangle([(x-30,y-12),(x+30,y+12)], outline=GREEN, width=2)
        draw.rounded_rectangle([(x-30,y-30),(x+30,y-14)], radius=4, fill=GREEN)
        draw.text((x,y-22), f"{lbl} {conf:.0%}", fill=WHITE, font=getfont(8), anchor="mm")
    draw.rounded_rectangle([(20,420),(W-20,520)], radius=12, fill=WHITE)
    draw.text((30,435), "👁️  I see:", fill=TEXT_DARK, font=getfont(12))
    draw.text((30,458), "person (94%), laptop (88%), cup (76%)", fill=GREEN, font=getfont(10))
    draw.text((30,480), "Looks like you're at your desk working!", fill=TEXT_MED, font=getfont(10))
    phone_frame(img)
    img.save(OUT / "step4-detection.png")
    print("  ✓ step4-detection.png")

def make_step5():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    status_bar(draw)
    draw.rectangle([(0,28),(W//2,H)], fill=BG)
    draw.rectangle([(W//2,28),(W,H)], fill=(241, 245, 249))
    draw.text((W//4,50), "Chat", fill=TEXT_DARK, font=getfont(13), anchor="mt")
    bubble = "Opening Maps for Pho!"
    draw.rounded_rectangle([(10,78),(160,105)], radius=10, fill=PURPLE)
    draw.text((20,85), bubble, fill=WHITE, font=getfont(10))
    draw.text((10,130), "Ask me about the results!", fill=TEXT_LIGHT, font=getfont(9))
    draw.text((W*0.75,50), "Google Maps", fill=TEXT_DARK, font=getfont(11), anchor="mt")
    for i, name in enumerate(["Pho 99 ★4.5","Saigon Bowl ★4.2","Taste Vietnam ★3.9"]):
        y = 80 + i*50
        draw.rounded_rectangle([(W//2+8,y),(W-8,y+42)], radius=6, fill=WHITE)
        draw.text((W//2+18,y+8), name, fill=TEXT_DARK, font=getfont(10))
        draw.text((W//2+18,y+26), "0.3 mi · Open now", fill=TEXT_LIGHT, font=getfont(8))
    draw.text((W//4,H-20), "← overlay mode", fill=TEXT_LIGHT, font=getfont(8), anchor="ms")
    phone_frame(img)
    img.save(OUT / "step5-maps-overlay.png")
    print("  ✓ step5-maps-overlay.png")

def make_step6():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    status_bar(draw)
    draw.rectangle([(0,28),(W//2,H)], fill=BG)
    draw.rectangle([(W//2,28),(W,H)], fill=(241, 245, 249))
    draw.text((W//4,50), "Controls", fill=TEXT_DARK, font=getfont(13), anchor="mt")
    cmds = [("scroll down","↕"),("tap / select","👆"),("cursor right","➡"),
            ("swipe up","⬆"),("go back","🔙"),("scroll 3x","↕×3")]
    for i,(c,icon) in enumerate(cmds):
        y = 85 + i*38
        draw.rounded_rectangle([(8,y),(W//2-8,y+30)], radius=8, fill=WHITE)
        draw.text((18,y+7), f"{icon}  {c}", fill=PINK, font=getfont(10))
    draw.rounded_rectangle([(W//2+10,70),(W-10,120)], radius=10, fill=WHITE)
    draw.text((W//2+20,82), "Crosshair", fill=TEXT_LIGHT, font=getfont(9))
    cx, cy = W*0.75, 95
    draw.line([(cx-15,cy),(cx+15,cy)], fill=PURPLE, width=2)
    draw.line([(cx,cy-15),(cx,cy+15)], fill=PURPLE, width=2)
    draw.ellipse([(cx-3,cy-3),(cx+3,cy+3)], fill=PURPLE)
    draw.text((W//4,H-20), "← overlay mode", fill=TEXT_LIGHT, font=getfont(8), anchor="ms")
    phone_frame(img)
    img.save(OUT / "step6-navigate.png")
    print("  ✓ step6-navigate.png")

def make_step7():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    status_bar(draw)
    draw.text((W//2,45), "Notification", fill=TEXT_DARK, font=getfont(14), anchor="mt")
    notifs = [
        ("📞 Missed Call", "Mom · 2 min ago", PURPLE, "Want me to call them back?"),
        ("✉️ Email", "Sarah: Re: Project Update", YELLOW, "Want me to read it?"),
        ("💬 SMS", "Dave: Coming to the party?", GREEN, "Want me to reply?"),
    ]
    for i,(title,sub,color,action) in enumerate(notifs):
        y = 72 + i*80
        draw.rounded_rectangle([(16,y),(W-16,y+68)], radius=10, fill=WHITE)
        draw.text((28,y+10), title, fill=TEXT_DARK, font=getfont(13))
        draw.text((28,y+30), sub, fill=TEXT_LIGHT, font=getfont(10))
    draw.rounded_rectangle([(30,320),(W-30,355)], radius=10, fill=WHITE)
    draw.text((W//2,337), "💡  Want me to call them back?", fill=TEXT_DARK, font=getfont(11), anchor="mm")
    draw.text((W//2,380), "Priority: OS tier → App tier", fill=TEXT_LIGHT, font=getfont(9), anchor="mt")
    phone_frame(img)
    img.save(OUT / "step7-notification.png")
    print("  ✓ step7-notification.png")

def make_step8():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    status_bar(draw)
    draw.rectangle([(0,28),(W,H)], fill=(241, 245, 249))
    draw.text((W//2,50), "Google Maps", fill=TEXT_DARK, font=getfont(16), anchor="mt")
    draw.rounded_rectangle([(20,80),(W-20,180)], radius=6, fill=WHITE)
    draw.text((W//2,110), "📍 Pho 99 Restaurant", fill=TEXT_DARK, font=getfont(14), anchor="mm")
    draw.text((W//2,135), "★ 4.5 · 0.3 mi · Open until 10pm", fill=YELLOW, font=getfont(10), anchor="mm")
    draw.text((W//2,155), "123 Main Street, Suite 4", fill=TEXT_LIGHT, font=getfont(9), anchor="mm")
    draw.rounded_rectangle([(20,210),(W-20,260)], radius=10, fill=GREEN)
    draw.text((W//2,235), "▶ Navigate", fill=WHITE, font=getfont(14), anchor="mm")
    draw.rounded_rectangle([(20,280),(W-20,310)], radius=8, fill=WHITE)
    draw.text((W//2,295), "More places nearby", fill=TEXT_DARK, font=getfont(11), anchor="mm")
    draw.text((W//2,H-15), "Full screen mode", fill=TEXT_LIGHT, font=getfont(9), anchor="ms")
    phone_frame(img)
    img.save(OUT / "step8-fullscreen.png")
    print("  ✓ step8-fullscreen.png")

def make_step9():
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    status_bar(draw)
    draw.rectangle([(0,28),(W,340)], fill=BG)
    draw.text((20,55), "YouTube", fill=TEXT_DARK, font=getfont(16))
    draw.rounded_rectangle([(20,90),(W-20,200)], radius=8, fill=WHITE)
    draw.text((W//2,145), "▶ Playing: Lo-Fi Study Beats", fill=TEXT_MED, font=getfont(12), anchor="mm")
    pip_x, pip_y = 180, 400
    draw.rounded_rectangle([(pip_x,pip_y),(pip_x+160,pip_y+120)], radius=12, fill=WHITE, outline=PINK, width=2)
    draw.text((pip_x+80,pip_y+40), "🎬", font=getfont(30), anchor="mm")
    draw.text((pip_x+80,pip_y+75), "YouTube PiP", fill=TEXT_DARK, font=getfont(9), anchor="mm")
    draw.text((16,420), "Chat:", fill=TEXT_LIGHT, font=getfont(11))
    draw.rounded_rectangle([(10,445),(160,475)], radius=10, fill=PURPLE)
    draw.text((18,452), '"what song is this?"', fill=WHITE, font=getfont(10))
    draw.rounded_rectangle([(10,490),(200,540)], radius=12, fill=WHITE)
    draw.text((20,500), "🤖 It's 'Lofi Girl - Study'\n   Great for focusing!", fill=TEXT_DARK, font=getfont(10))
    draw.text((W//2,580), "Sensor skill: light < 10 lux", fill=TEXT_LIGHT, font=getfont(9), anchor="mt")
    draw.rounded_rectangle([(30,600),(W-30,635)], radius=10, fill=WHITE)
    draw.text((W//2,617), '"It got dark! Want me to turn on a light?"', fill=PINK, font=getfont(10), anchor="mm")
    phone_frame(img)
    img.save(OUT / "step9-youtube-pip.png")
    print("  ✓ step9-youtube-pip.png")

if __name__ == "__main__":
    print("Generating pastel demo screenshots...")
    make_step1()
    make_step2()
    make_step3()
    make_step4()
    make_step5()
    make_step6()
    make_step7()
    make_step8()
    make_step9()
    print(f"\nDone! {len(list(OUT.glob('*.png')))} screenshots in {OUT}/")
