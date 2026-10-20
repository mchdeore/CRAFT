import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { TooltipProvider } from '@/components/ui/tooltip'
import './index.css'
import App from '@/App'

const backendUnavailable = import.meta.env.PROD &&
  !import.meta.env.VITE_API_ORIGIN?.trim() &&
  globalThis.location.hostname.endsWith('.github.io')

// In mock mode (npm run dev:mock) a stand-in backend answers API requests in
// the browser, so the app runs without a CRAFT backend.
async function startMockBackend() {
  if (import.meta.env.VITE_MOCK !== 'true') return
  const { worker } = await import('@/mocks/browser')
  await worker.start({ onUnhandledFrame: 'bypass' })
}

void startMockBackend().then(() => {
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <TooltipProvider>
        {backendUnavailable ?
          <main className="mx-auto flex min-h-dvh max-w-xl flex-col justify-center gap-4 px-6">
            <h1 className="text-2xl font-semibold">CRAFT</h1>
            <p role="status">Sign-in is unavailable while the demo backend is offline.</p>
            <a className="underline" href="../preview/">Try the simulated scratchpad preview</a>
            <a className="underline" href="../">Read the documentation</a>
          </main> : <App />}
      </TooltipProvider>
    </StrictMode>,
  )
})
