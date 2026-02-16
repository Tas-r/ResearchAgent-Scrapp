import './App.css'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

const SUGGESTIONS = [
  'Find PubMed articles on Alzheimer\'s and aging',
  'Search for studies on diabetes treatment 2020-2024',
  'Get research on machine learning in healthcare',
  'Papers about COVID-19 vaccine efficacy',
]

function tryParsePubMed(content) {
  if (typeof content !== 'string') return null
  let str = content.trim()
  const codeBlock = str.match(/```(?:json)?\s*([\s\S]*?)```/)
  if (codeBlock) str = codeBlock[1].trim()
  const jsonMatch = str.match(/\{[\s\S]*\}/)
  if (!jsonMatch) return null
  try {
    const data = JSON.parse(jsonMatch[0])
    if (data?.results && Array.isArray(data.results)) {
      return data
    }
    return null
  } catch {
    return null
  }
}

function MessageContent({ content, role }) {
  const pubmed = tryParsePubMed(content)

  if (pubmed) {
    return (
      <div className="pubmed-results">
        <div className="pubmed-meta">
          <span><strong>{pubmed.total_results}</strong> results</span>
          {pubmed.query && <span>Query: {pubmed.query}</span>}
        </div>
        <div className="pubmed-cards">
          {pubmed.results.map((r, i) => (
            <article key={r.pmid || i} className="pubmed-card">
              <h3 className="pubmed-card-title">
                <a href={r.url} target="_blank" rel="noopener noreferrer">
                  {r.title || 'Untitled'}
                </a>
              </h3>
              {r.authors && (
                <p className="pubmed-card-meta">{r.authors}</p>
              )}
              {r.journal_citation && (
                <p className="pubmed-card-citation">{r.journal_citation}</p>
              )}
              {r.snippet && (
                <p className="pubmed-card-snippet">{r.snippet}</p>
              )}
              <a
                href={r.url}
                target="_blank"
                rel="noopener noreferrer"
                className="pubmed-card-link"
              >
                View on PubMed →
              </a>
            </article>
          ))}
        </div>
      </div>
    )
  }

  return <div className="message-text">{content}</div>
}

function App() {
  const [messages, setMessages] = useState([])
  const [input, setInput] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const chatEndRef = useRef(null)

  const canSend = useMemo(() => input.trim().length > 0 && !loading, [input, loading])
  const showWelcome = messages.length === 0 && !loading

  const scrollToBottom = useCallback(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [])

  useEffect(() => {
    scrollToBottom()
  }, [messages, loading, scrollToBottom])

  async function send(userText) {
    const text = (typeof userText === 'string' ? userText : input.trim()).trim()
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
        body: JSON.stringify({
          messages: nextMessages.filter((m) => m.role === 'user' || m.role === 'assistant'),
        }),
      })
      const data = await resp.json()
      if (!resp.ok) {
        throw new Error(data?.message || data?.error || `HTTP ${resp.status}`)
      }
      setMessages(data.messages || nextMessages)
    } catch (e) {
      setError(String(e?.message || e))
      setMessages((prev) => prev.slice(0, -1))
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
    <div className="app">
      <header className="header">
        <div className="header-inner">
          <div className="header-icon">🔬</div>
          <div className="header-text">
            <h1>Research Agent</h1>
            <p>AI-powered PubMed search — ask for articles and get structured results</p>
          </div>
        </div>
      </header>

      <main className="chat-area">
        {showWelcome && (
          <div className="welcome-state">
            <div className="welcome-icon">🔬</div>
            <h2>How can I help?</h2>
            <p>
              Ask me to find research articles from PubMed. I'll search and return
              structured results with titles, authors, citations, and links.
            </p>
            <div className="suggestions">
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="suggestion-chip"
                  onClick={() => send(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((m, idx) => (
          <div key={idx} className={`message ${m.role}`}>
            <div className="message-avatar">
              {m.role === 'assistant' ? 'AI' : 'You'}
            </div>
            <div className="message-body">
              <div className="message-content">
                <MessageContent content={m.content} role={m.role} />
              </div>
            </div>
          </div>
        ))}

        {loading && (
          <div className="message assistant">
            <div className="message-avatar">AI</div>
            <div className="message-body">
              <div className="loading-indicator">
                <div className="loading-dots">
                  <span />
                  <span />
                  <span />
                </div>
                <span className="loading-text">Searching PubMed…</span>
              </div>
            </div>
          </div>
        )}

        <div ref={chatEndRef} />
      </main>

      <div className="input-area">
        <div className="input-wrapper">
          <textarea
            className="input-field"
            rows={1}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="Ask for PubMed articles, e.g. studies on diabetes 2020-2024, max 10"
            disabled={loading}
          />
          <button
            type="button"
            className="send-btn"
            onClick={() => send()}
            disabled={!canSend}
            aria-label="Send"
          >
            →
          </button>
        </div>
        {error && <div className="error-banner">{error}</div>}
      </div>
    </div>
  )
}

export default App
