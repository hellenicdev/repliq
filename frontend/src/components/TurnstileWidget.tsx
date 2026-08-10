import { useEffect, useRef } from 'react'

declare global {
  interface Window {
    turnstile?: {
      render: (el: HTMLElement, opts: Record<string, unknown>) => string
      reset: (widgetId?: string) => void
      remove: (widgetId?: string) => void
    }
  }
}

const SCRIPT_ID = 'turnstile-script'
const SITE_KEY = import.meta.env.VITE_TURNSTILE_SITE_KEY as string | undefined

interface Props {
  onToken: (token: string | null) => void
}

/** Cloudflare Turnstile widget (explicit rendering, no extra dependency). */
export default function TurnstileWidget({ onToken }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const widgetIdRef = useRef<string | null>(null)
  const callbackRef = useRef(onToken)
  callbackRef.current = onToken

  useEffect(() => {
    if (!SITE_KEY || !containerRef.current) return

    const render = () => {
      if (!window.turnstile || !containerRef.current) return
      widgetIdRef.current = window.turnstile.render(containerRef.current, {
        sitekey: SITE_KEY,
        theme: 'light',
        callback: (token: string) => callbackRef.current(token),
        'expired-callback': () => callbackRef.current(null),
        'error-callback': () => callbackRef.current(null),
      })
    }

    if (window.turnstile) {
      render()
    } else {
      let script = document.getElementById(SCRIPT_ID) as HTMLScriptElement | null
      if (!script) {
        script = document.createElement('script')
        script.id = SCRIPT_ID
        script.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit'
        script.async = true
        script.defer = true
        document.head.appendChild(script)
      }
      script.addEventListener('load', render)
    }

    return () => {
      if (widgetIdRef.current && window.turnstile) {
        window.turnstile.remove(widgetIdRef.current)
      }
    }
  }, [])

  if (!SITE_KEY) {
    return (
      <p className="hint">
        Turnstile not configured (set <code>VITE_TURNSTILE_SITE_KEY</code> in frontend/.env). Backend
        will reject requests until it is.
      </p>
    )
  }

  return <div ref={containerRef} />
}

/** Reset the widget so a fresh token is issued (e.g. after a generation). */
export function resetTurnstile() {
  if (window.turnstile) window.turnstile.reset()
}
