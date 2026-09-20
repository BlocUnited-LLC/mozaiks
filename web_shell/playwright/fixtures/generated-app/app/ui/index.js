import GatedFeaturePage from './pages/custom/GatedFeaturePage.jsx'

export function register(registerComponent) {
  if (typeof registerComponent === 'function') {
    registerComponent('GatedFeaturePage', GatedFeaturePage)
  }
}
