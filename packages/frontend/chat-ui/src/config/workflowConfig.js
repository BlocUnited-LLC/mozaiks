// ==============================================================================
// FILE: chat-ui/src/config/workflowConfig.js
// DESCRIPTION: Dynamic workflow configuration for frontend
// ==============================================================================

/**
 * Workflow configuration manager for frontend
 * Fetches workflow configurations from backend
 */
import config from './index';

class WorkflowConfig {
  constructor() {
    this.configs = new Map();
    this.defaultWorkflow = null; // Dynamic discovery from backend
    this.fetchInProgress = false;
    this.warnedNoWorkflow = false;
    this.autoFetchAttempted = false;
  }

  /**
   * Fetch workflow configurations from backend
   */
  async fetchWorkflowConfigs() {
    if (this.fetchInProgress) {
      return; // prevent concurrent duplicate fetches (StrictMode double invoke)
    }
    this.fetchInProgress = true;
    const baseUrlRaw = typeof config?.get === 'function' ? config.get('api.baseUrl') : undefined;
    const baseUrl = typeof baseUrlRaw === 'string' && baseUrlRaw.endsWith('/') ? baseUrlRaw.slice(0, -1) : baseUrlRaw;
    const hosts = [
      baseUrl,
      'http://localhost:8080',
      'http://127.0.0.1:8080'
    ].filter(Boolean);
    const path = '/api/workflows';
    let lastError = null;
    for (const host of hosts) {
      const url = host + path;
      try {
        const controller = new AbortController();
        const timeout = setTimeout(() => controller.abort(), 5000);
        console.log('� WorkflowRegistry: Fetching workflows from', url);
        const response = await fetch(url, { signal: controller.signal });
        clearTimeout(timeout);
        if (!response.ok) {
          const txt = await response.text().catch(()=> '');
          console.warn('⚠️ Workflow fetch non-OK', response.status, txt.slice(0,200));
          lastError = new Error('status_'+response.status);
          continue;
        }
        const data = await response.json();
        console.log('🔍 Raw workflow data from backend:', data);
        const workflows = [];
        for (const [workflowName, wfCfg] of Object.entries(data)) {
          workflows.push({ workflow_name: workflowName, ...wfCfg });
        }
        for (const workflow of workflows) {
          this.configs.set(workflow.workflow_name, workflow);
          const lowerKey = workflow.workflow_name.toLowerCase();
          if (!this.configs.has(lowerKey)) this.configs.set(lowerKey, workflow);
        }
        console.log('✅ Loaded workflow configs:', workflows.map(w => w.workflow_name));
        if (workflows.length > 0) {
          if (!this.defaultWorkflow) {
            this.defaultWorkflow = workflows[0].workflow_name;
            console.log('🎯 Default workflow set to:', this.defaultWorkflow);
          }
        }
        this.fetchInProgress = false;
        this.warnedNoWorkflow = false;
        return; // success
      } catch (error) {
        lastError = error;
        if (error.name === 'AbortError') {
          console.warn('⚠️ Workflow fetch timeout for', url);
        } else {
          console.warn('⚠️ Workflow fetch failed for', url, error.message);
        }
        // try next host
      }
    }
    if (lastError) {
      console.warn('⚠️ All workflow fetch attempts failed. Operating with no configs. Last error:', lastError.message);
    }
    this.fetchInProgress = false;
  }

  /**
   * Get available workflow types
   */
  getAvailableWorkflows() {
    return Array.from(this.configs.keys());
  }

  /**
   * Get default workflow type
   */
  getDefaultWorkflow() {
    if (this.defaultWorkflow) {
      return this.defaultWorkflow;
    }

    const available = this.getAvailableWorkflows();
    if (available.length > 0) {
      return available[0]; // Use first available workflow
    }

    // If called before async discovery completes (common in React StrictMode),
    // trigger a single background fetch and avoid noisy warnings.
    if (!this.fetchInProgress && !this.autoFetchAttempted) {
      this.autoFetchAttempted = true;
      try {
        this.fetchWorkflowConfigs().catch(() => {});
      } catch (_e) {
        // ignore
      }
      return null;
    }

    if (this.fetchInProgress) {
      // Avoid noisy warnings while discovery is still running
      return null;
    }

    if (!this.warnedNoWorkflow) {
      console.warn('⚠️ No workflows discovered, backend should provide default');
      this.warnedNoWorkflow = true;
    }
    return null;
  }

  /**
   * Get workflow configuration by type
   */
  getWorkflowConfig(workflowname) {
    if (!workflowname) return null;
    // Direct hit (exact case or lowercase alias already inserted)
    const direct = this.configs.get(workflowname);
    if (direct) return direct;
    // Fallback: case-insensitive scan
    const target = workflowname.toLowerCase();
    for (const [k, v] of this.configs.entries()) {
      if (k.toLowerCase() === target) return v;
    }
    return null;
  }

  /**
   * Check if workflow has human in the loop
   */
  hasHumanInTheLoop(workflowname) {
    const config = this.configs.get(workflowname);
    return config?.human_in_the_loop || false;
  }

  /**
   * Get inline component agents for workflow
   */
  getInlineComponentAgents(workflowname) {
    const config = this.configs.get(workflowname);
    return config?.inline_component_agents || [];
  }

  /**
   * Get artifact component agents for workflow
   */
  getArtifactComponentAgents(workflowname) {
    const config = this.configs.get(workflowname);
    return config?.artifact_component_agents || [];
  }

  /**
   * Check if workflow has UserProxy component integration
   */
  hasUserProxyComponent(workflowname) {
    const config = this.configs.get(workflowname);
    const visualAgents = Array.isArray(config?.visual_agents) ? config.visual_agents : [];
    return visualAgents.some(agentName => {
      if (typeof agentName !== 'string') return false;
      const lowered = agentName.toLowerCase();
      return lowered.includes('user') || lowered.includes('proxy');
    });
  }

  /**
   * Get UserProxy-related components for workflow
   */
  getUserProxyComponents(workflowname) {
    const config = this.configs.get(workflowname);
    const visualAgents = Array.isArray(config?.visual_agents) ? config.visual_agents : [];
    return visualAgents.filter(agentName => {
      if (typeof agentName !== 'string') return false;
      const lowered = agentName.toLowerCase();
      return lowered.includes('user') || lowered.includes('proxy');
    });
  }

  /**
   * Check if UserProxy needs special transport handling
   */
  userProxyNeedsTransportHandling(workflowname) {
    // UserProxy with human_in_the_loop=true needs transport integration
    return this.hasHumanInTheLoop(workflowname);
  }
}

// Global instance
export const workflowConfig = new WorkflowConfig();
export default workflowConfig;
