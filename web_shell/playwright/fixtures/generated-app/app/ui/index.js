import GatedFeaturePage from './pages/custom/GatedFeaturePage.jsx'
import TokenDepletionPage from './pages/custom/TokenDepletionPage.jsx'

export function register(registerComponent) {
  if (typeof registerComponent === 'function') {
    registerComponent('GatedFeaturePage', GatedFeaturePage)
    registerComponent('TokenDepletionPage', TokenDepletionPage)
  }
}
