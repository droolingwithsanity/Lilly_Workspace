import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App.jsx'

try {
  ReactDOM.createRoot(document.getElementById('root')).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>
  )
  window.__hideLoading()
} catch (e) {
  document.getElementById('app-loading').innerHTML =
    '<h1 style="color:#c00">Failed to load</h1><p style="color:#666">' + e.message + '</p>'
  console.error('React mount error:', e)
}
