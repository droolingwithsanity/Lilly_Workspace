const canvas = document.getElementById("c");
const ctx = canvas.getContext("2d");

const input = document.getElementById("input");
const text = document.getElementById("text");

let w, h;
function resize(){
  w = canvas.width = innerWidth;
  h = canvas.height = innerHeight;
}
resize();
addEventListener("resize", resize);

/* ---------------- SPHERE AGENT (YOUR LILLY CORE) ---------------- */

let particles = [];
const COUNT = 2500;
const R = 160;

class Node {
  constructor(){
    this.a = Math.random() * Math.PI * 2;
    this.r = R + Math.random() * 20;
  }

  update(){
    const t = performance.now() * 0.001;

    this.r += Math.sin(this.a * 5 + t) * 0.2;

    this.x = w/2 + Math.cos(this.a) * this.r;
    this.y = h/2 + Math.sin(this.a) * this.r;
  }

  draw(){
    ctx.fillStyle = "rgba(0,0,0,0.6)";
    ctx.beginPath();
    ctx.arc(this.x, this.y, 1.2, 0, Math.PI*2);
    ctx.fill();
  }
}

for(let i=0;i<COUNT;i++) particles.push(new Node());

function render(){
  ctx.fillStyle = "rgba(255,255,255,0.1)";
  ctx.fillRect(0,0,w,h);

  particles.forEach(p => {
    p.update();
    p.draw();
  });

  requestAnimationFrame(render);
}
render();

/* ---------------- OPENWEBUI API ---------------- */

async function ask(prompt){

  const res = await fetch("http://localhost:3001/api/chat/completions", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Authorization": "Bearer ollama"
    },
    body: JSON.stringify({
      model: "lilly",
      messages: [
        { role: "system", content: "You are Lilly, a living sphere intelligence." },
        { role: "user", content: prompt }
      ],
      stream: false
    })
  });

  const data = await res.json();

  const msg =
    data.choices?.[0]?.message?.content ||
    "..."

  type(msg);
}

/* ---------------- TYPE EFFECT ---------------- */

function type(txt){
  text.innerText = "";
  let i = 0;

  const t = setInterval(()=>{
    text.innerText += txt[i++];
    if(i >= txt.length) clearInterval(t);
  }, 12);
}

/* ---------------- INPUT ---------------- */

input.addEventListener("keydown", e=>{
  if(e.key === "Enter"){
    ask(input.value);
    input.value = "";
  }
});
