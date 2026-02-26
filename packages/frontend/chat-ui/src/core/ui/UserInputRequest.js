// ==============================================================================
// FILE: chat-ui/src/core/ui/UserInputRequest.js
// DESCRIPTION: Generic user input component for AG2 agent requests
// PURPOSE: Reusable user input component for any workflow
// ==============================================================================

import React, { useState, useCallback } from 'react';
import { FiMessageCircle, FiSend, FiX } from 'react-icons/fi';
import config from '../../config';

/**
 * 🎯 GENERIC USER INPUT REQUEST COMPONENT
 * 
 * Handles any user input requests from AG2 agents during workflow execution.
 * This is triggered when agents use input() or need user feedback.
 * 
 * USAGE:
 * - Any workflow can use this for generic user input
 * - Works with any AG2 agent that sends input requests
 * - Workflow-agnostic and reusable
 * - Uses WebSocket first, falls back to REST (F5)
 */
const UserInputRequest = ({ payload, onResponse, onCancel, submitInputRequest }) => {
  const [userInput, setUserInput] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const {
    input_request_id,
    prompt = "Input required:",
    password = false,
    // chat_id, app_id, timestamp - unused for now
  } = payload || {};

  const handleSubmit = useCallback(async (e) => {
    e?.preventDefault();
    
    if (isSubmitting) return;
    
    setIsSubmitting(true);
    
    try {
      let success = false;
      
      // F5: Try WebSocket first if available
      if (submitInputRequest && typeof submitInputRequest === 'function') {
        try {
          success = await submitInputRequest(input_request_id, userInput || "");
          if (success) {
            console.log(`✅ UserInputRequest (WebSocket): Sent response for ${input_request_id}`);
          }
        } catch (wsError) {
          console.warn(`⚠️ UserInputRequest: WebSocket failed, falling back to REST:`, wsError);
        }
      }
      
      // Fall back to REST if WebSocket failed or unavailable
      if (!success) {
        const baseUrlRaw = typeof config?.get === 'function' ? config.get('api.baseUrl') : undefined;
        const baseUrl = typeof baseUrlRaw === 'string' && baseUrlRaw.endsWith('/') ? baseUrlRaw.slice(0, -1) : (baseUrlRaw || 'http://localhost:8080');
        const response = await fetch(`${baseUrl}/api/user-input/submit`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            input_request_id,
            user_input: userInput || "" // Empty string for enter/skip
          })
        });
        
        if (response.ok) {
          const result = await response.json();
          console.log(`✅ UserInputRequest (REST): Sent response for ${input_request_id}:`, result);
          success = true;
        } else {
          const error = await response.text();
          console.error(`❌ UserInputRequest: REST also failed:`, error);
          throw new Error(`HTTP ${response.status}: ${error}`);
        }
      }
      
      // Call onResponse if provided for cleanup/notification
      if (success && onResponse) {
        await onResponse({
          input_request_id,
          user_response: userInput || "",
          status: 'submitted'
        });
      }
      
    } catch (error) {
      console.error(`❌ UserInputRequest: All methods failed:`, error);
    } finally {
      setIsSubmitting(false);
    }
  }, [userInput, input_request_id, onResponse, isSubmitting, submitInputRequest]);

  const handleSkip = useCallback(async () => {
    // Send empty response (equivalent to pressing enter)
    setUserInput('');
    await handleSubmit();
  }, [handleSubmit]);

  const handleCancel = useCallback(() => {
    if (onCancel) {
      onCancel({
        input_request_id,
        reason: 'user_cancelled'
      });
    }
  }, [input_request_id, onCancel]);

  const handleKeyPress = useCallback((e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  }, [handleSubmit]);

  return (
    <div className="user-input-request-container bg-[var(--color-surface)] border-l-4 border-[rgba(var(--color-primary-rgb),0.6)] p-4 mb-4 rounded-r-lg shadow-lg shadow-[rgba(var(--color-primary-rgb),0.12)] backdrop-blur-sm">
      <div className="flex items-start space-x-3">
        <div className="flex-shrink-0">
          <FiMessageCircle className="h-5 w-5 text-[var(--color-primary-light)] mt-0.5" />
        </div>
        
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-[var(--color-primary-light)] mb-2">
            Agent Input Request
          </div>
          
          <div className="text-sm text-[var(--color-text-secondary)] mb-3">
            {prompt}
          </div>
          
          <form onSubmit={handleSubmit} className="space-y-3">
            <div className="flex items-center space-x-2">
              <input
                type={password ? "password" : "text"}
                value={userInput}
                onChange={(e) => setUserInput(e.target.value)}
                onKeyPress={handleKeyPress}
                placeholder="Type your response or press Enter to skip..."
                className="flex-1 px-3 py-2 border border-[rgba(var(--color-primary-rgb),0.4)] rounded-md text-sm bg-[rgba(var(--color-surface-alt-rgb),0.4)] text-[var(--color-text-primary)] placeholder-[rgba(var(--color-text-secondary-rgb,148,163,184),0.7)] focus:ring-2 focus:ring-[rgba(var(--color-primary-rgb),0.6)] focus:border-[var(--color-primary)]"
                disabled={isSubmitting}
                autoFocus
              />
              
              <button
                type="submit"
                disabled={isSubmitting}
                className="inline-flex items-center px-3 py-2 border border-transparent text-sm font-medium rounded-md text-[var(--color-text-on-accent)] bg-[var(--color-primary)] hover:bg-[var(--color-primary-light)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--color-primary)] disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <FiSend className="h-4 w-4" />
              </button>
              
              <button
                type="button"
                onClick={handleSkip}
                disabled={isSubmitting}
                className="inline-flex items-center px-3 py-2 border border-[rgba(var(--color-primary-rgb),0.35)] text-sm font-medium rounded-md text-[var(--color-text-secondary)] bg-[rgba(var(--color-surface-rgb),0.85)] hover:bg-[rgba(var(--color-surface-rgb),0.95)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--color-primary-light)] disabled:opacity-50 disabled:cursor-not-allowed"
              >
                Skip
              </button>
              
              {onCancel && (
                <button
                  type="button"
                  onClick={handleCancel}
                  disabled={isSubmitting}
                  className="inline-flex items-center px-2 py-2 border border-[rgba(var(--color-error-rgb),0.5)] text-sm font-medium rounded-md text-[var(--color-error)] bg-[rgba(var(--color-error-rgb),0.08)] hover:bg-[rgba(var(--color-error-rgb),0.15)] focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-[var(--color-error)] disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  <FiX className="h-4 w-4" />
                </button>
              )}
            </div>
            
            <div className="text-xs text-[var(--color-text-muted)]">
              Press Enter to submit, or click Skip to continue without input
            </div>
          </form>
        </div>
      </div>
      
      {isSubmitting && (
        <div className="mt-2 text-xs text-[var(--color-primary-light)]">
          Sending response...
        </div>
      )}
    </div>
  );
};

export default UserInputRequest;
