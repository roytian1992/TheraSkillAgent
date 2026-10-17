import App from './App'
import './styles.css'

const plugins = (window as unknown as { __HERMES_PLUGINS__?: { register: (name: string, component: unknown) => void } }).__HERMES_PLUGINS__
if (plugins) plugins.register('theraskill-support', App)
