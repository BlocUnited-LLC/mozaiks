// ==============================================================================
// FILE: chat-ui/src/config/workflowConfig.js
// DESCRIPTION: Dynamic workflow configuration for frontend
// ==============================================================================

/**
 * Workflow configuration manager for frontend
 * Fetches workflow configurations from backend
 */
import config from './index';
import platform from '../platform/index.js';

function getAccessToken() {
  return platform.getAccessToken();
}

function normalizeWorkflowRecord(record, fallbackName = null) {
  if (!record || typeof record !== 'object' || Array.isArray(record)) {
    return null;
  }

  const workflowName = String(record.workflow_name || record.name || fallbackName || '').trim();
  if (!workflowName) {
    return null;
  }

  return {
    ...record,
    workflow_name: workflowName,
    name: workflowName,
    display_name: record.display_name || workflowName,
  };
}

function extractWorkflowRecords(payload) {
  if (Array.isArray(payload)) {
    return payload
      .map((record) => normalizeWorkflowRecord(record))
      .filter(Boolean);
  }

  if (Array.isArray(payload?.workflows)) {
    return payload.workflows
      .map((record) => normalizeWorkflowRecord(record))
      .filter(Boolean);
  }

  if (payload?.workflows && typeof payload.workflows === 'object') {
    return Object.entries(payload.workflows)
      .map(([key, record]) => normalizeWorkflowRecord(record, key))
      .filter(Boolean);
  }

  if (payload && typeof payload === 'object') {
    return Object.entries(payload)
      .map(([key, record]) => normalizeWorkflowRecord(record, key))
      .filter(Boolean);
  }

  return [];
}

class WorkflowConfig {
  constructor() {
    this.configs = new Map();
    this.defaultWorkflow = null; // Dynamic discovery from backend
    this.fetchInProgress = false;
    this.fetchPromise = null;
    this.warnedNoWorkflow = false;
    this.autoFetchAttempted = false;
  }

  /**
   * Fetch workflow configurations from backend
   */
  async fetchWorkflowConfigs() {
    if (this.fetchInProgress) {
      return this.fetchPromise; // wait for the real in-flight fetch to finish
    }
    this.fetchInProgress = true;
    this.fetchPromise = (async () => {
      const baseUrlRaw = typeof config?.get === 'function' ? config.get('api.baseUrl') : undefined;
      const baseUrl = typeof baseUrlRaw === 'string' && baseUrlRaw.endsWith('/') ? baseUrlRaw.slice(0, -1) : baseUrlRaw;
      const path = '/api/workflows';
      const urls = [];
      if (typeof window !== 'undefined') {
        urls.push(path);
      }
      if (baseUrl) {
        urls.push(baseUrl + path);
      }
      const candidates = Array.from(new Set(urls));
      const token = getAccessToken();
      const headers = token ? { Authorization: `Bearer ${token}` } : {};
      let lastError = null;
      for (const url of candidates) {
        try {
          const controller = new AbortController();
          const timeout = setTimeout(() => controller.abort(), 5000);
          const response = await fetch(url, { signal: controller.signal, headers });
          clearTimeout(timeout);
          if (!response.ok) {
            const txt = await response.text().catch(()=> '');
            console.warn('⚠️ Workflow fetch non-OK', response.status, txt.slice(0,200));
            lastError = new Error('status_'+response.status);
            continue;
          }
          const data = await response.json();
          const workflows = extractWorkflowRecords(data);


          this.configs.clear();
          for (const workflow of workflows) {
            this.configs.set(workflow.workflow_name, workflow);
            const lowerKey = workflow.workflow_name.toLowerCase();
            if (!this.configs.has(lowerKey)) this.configs.set(lowerKey, workflow);
          }
          if (workflows.length > 0) {
            const entryPointWorkflow = workflows.find((workflow) => workflow?.entry_point === true)?.workflow_name;
            this.defaultWorkflow = entryPointWorkflow || workflows[0].workflow_name;
          } else {
            this.defaultWorkflow = null;
          }
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
    })().finally(() => {
      this.fetchInProgress = false;
      this.fetchPromise = null;
    });

    return this.fetchPromise;
  }

  /**
   * Get available workflow types
   */
  getAvailableWorkflows() {
    const workflows = [];
    for (const [key, workflow] of this.configs.entries()) {
      if (!workflow?.workflow_name) continue;
      if (key !== workflow.workflow_name) continue;
      workflows.push(workflow.workflow_name);
    }
    return workflows;
  }

  /**
   * Get the workflow marked as entry_point in backend config.
   * Canonical source is app/config/ai.json; backend projects it onto
   * the workflow configs returned by /api/workflows.
   * Returns null if no workflow has entry_point: true or configs aren't loaded yet.
   */
  getEntryPointWorkflow() {
    for (const [key, cfg] of this.configs.entries()) {
      // Skip lowercase aliases (they duplicate the canonical entry)
      if (key !== cfg?.workflow_name) continue;
      if (cfg?.entry_point === true) return cfg.workflow_name;
    }
    return null;
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

  resolveKnownWorkflowName(workflowname) {
    if (!workflowname) return null;
    const resolved = this.getWorkflowConfig(workflowname);
    return resolved?.workflow_name || null;
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
