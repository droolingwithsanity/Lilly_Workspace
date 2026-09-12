import React, { useState } from "react";
import ReactDOM from "react-dom/client";

function App() {
  const [input, setInput] = useState("");
  const [response, setResponse] = useState("");

  const send = async () => {
    const res = await fetch(`http://backend:8000/chat?prompt=\${input}`);
    const data = await res.json();
    setResponse(data.response || JSON.stringify(data));
  };

  return (
    <div style={{ padding: 20 }}>
      <h1>Lilly</h1>
      <input value={input} onChange={e => setInput(e.target.value)} />
      <button onClick={send}>Send</button>
      <pre>{response}</pre>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
