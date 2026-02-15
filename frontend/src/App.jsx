import './App.css'
import { useMemo, useState } from 'react'

function App() {
  const [messages, setMessages] = useState([
    { role: 'assistant', content: 'Hi! Ask me anything. If you ask for PubMed results, I will return JSON.' },
  ])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading])

  async function send() {
    const text = input.trim()
    if (!text || loading) return
    setError('')
    setLoading(true)

    const nextMessages = [...messages, { role: 'user', content: text }]
    setMessages(nextMessages)
    setInput('')

    try {
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: nextMessages.filter((m) => m.role === 'user' || m.role === 'assistant') }),
      })
      const data = await resp.json()
      if (!resp.ok) {
        throw new Error(data?.message || data?.error || `HTTP ${resp.status}`)
      }
      setMessages(data.messages || nextMessages)
    } catch (e) {
      setError(String(e?.message || e))
    } finally {
      setLoading(false)
    }
  }

  function onKeyDown(e) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      send()
    }
  }

  return (
    <div style={{ maxWidth: 900, margin: '0 auto', padding: 24 }}>
      <h2 style={{ marginTop: 0 }}>Research Agent (Django + React)</h2>

      <div
        style={{
          border: '1px solid rgba(255,255,255,0.2)',
          borderRadius: 12,
          padding: 16,
          minHeight: 420,
          background: 'rgba(0,0,0,0.15)',
        }}
      >
        {messages.map((m, idx) => (
          <div key={idx} style={{ marginBottom: 12 }}>
            <div style={{ fontSize: 12, opacity: 0.8, marginBottom: 4 }}>{m.role}</div>
            <pre style={{ margin: 0, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.content}</pre>
          </div>
        ))}
        {loading && <div style={{ opacity: 0.8 }}>Thinking…</div>}
      </div>

      <div style={{ marginTop: 16 }}>
        <textarea
          rows={3}
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Type a message. Example: Give me PubMed results for alzheimers and aging between 2015 and 2018, max 5"
          style={{ width: '100%', padding: 12, borderRadius: 12 }}
        />
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginTop: 12 }}>
          <button onClick={send} disabled={!canSend}>
            Send
          </button>
          {error && <div style={{ color: '#ff6b6b' }}>{error}</div>}
        </div>
      </div>
    </div>
  )
}

export default App
