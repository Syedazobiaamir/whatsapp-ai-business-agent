// TypeScript mirrors of the backend's Pydantic response schemas.

export type Role = "admin" | "sales_agent" | "viewer";
export type Temperature = "hot" | "warm" | "cold";
export type LeadStatus = "new" | "contacted" | "qualified" | "proposal" | "won" | "lost";
export const LEAD_STATUSES: LeadStatus[] = ["new", "contacted", "qualified", "proposal", "won", "lost"];

export interface Page<T> {
  items: T[];
  total: number;
  page: number;
  size: number;
}

export interface Agent {
  id: string;
  full_name: string;
  email: string;
  phone: string | null;
  role: Role;
  is_active: boolean;
  last_login_at: string | null;
  created_at: string;
}

export interface Customer {
  id: string;
  wa_id: string;
  name: string | null;
  email: string | null;
  company: string | null;
  notes: string | null;
  marketing_opt_in: boolean;
  opted_out_at: string | null;
  last_seen_at: string | null;
  created_at: string;
}

export interface LeadActivity {
  id: string;
  activity_type: string;
  description: string;
  actor_type: string;
  actor_agent_id: string | null;
  created_at: string;
}

export interface Lead {
  id: string;
  customer: Customer;
  conversation_id: string | null;
  assigned_agent_id: string | null;
  temperature: Temperature;
  score: number;
  qualification_reason: string | null;
  status: LeadStatus;
  requirements: string | null;
  interested_services: string[] | null;
  budget_text: string | null;
  timeline: string | null;
  updated_at: string;
  created_at: string;
}

export interface LeadDetail extends Lead {
  activities: LeadActivity[];
}

export interface Message {
  id: string;
  direction: "inbound" | "outbound";
  sender_type: "customer" | "ai" | "human" | "system";
  body: string | null;
  intent: string | null;
  ai_metadata: Record<string, unknown> | null;
  send_status: string;
  error_message: string | null;
  created_at: string;
}

export interface Conversation {
  id: string;
  customer: Customer;
  status: "open" | "closed";
  mode: "ai" | "human";
  assigned_agent_id: string | null;
  last_customer_message_at: string | null;
  last_message_at: string | null;
  created_at: string;
  last_message_preview: string | null;
  has_pending_handoff: boolean;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
  within_24h_window: boolean;
}

export interface Handoff {
  id: string;
  conversation_id: string;
  requested_by: string;
  reason: string;
  status: string;
  assigned_agent_id: string | null;
  created_at: string;
}

export interface OverviewStats {
  total_customers: number;
  total_leads: number;
  hot_leads: number;
  warm_leads: number;
  cold_leads: number;
  active_conversations: number;
  conversations_in_human_mode: number;
  pending_handoffs: number;
  automation_failures_open: number;
  leads_by_status: Record<string, number>;
  messages_last_24h: number;
}

export interface FAQ {
  id: string;
  question: string;
  answer: string;
  category: string;
  keywords: string | null;
  priority: number;
  is_active: boolean;
}

export interface Service {
  id: string;
  name: string;
  description: string;
  price_from: string | null;
  price_to: string | null;
  currency: string;
  is_active: boolean;
}

export interface Setting {
  key: string;
  value: Record<string, unknown>;
  updated_at: string;
}

export interface FollowUp {
  id: string;
  lead_id: string;
  customer_name: string | null;
  customer_wa_id: string | null;
  scheduled_at: string;
  message_type: string;
  template_name: string | null;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
}

export interface AutomationError {
  id: string;
  source: string;
  workflow_name: string | null;
  node_name: string | null;
  error_type: string;
  risk_level: "safe" | "risky";
  error_message: string;
  status: string;
  retry_count: number;
  max_retries: number;
  created_at: string;
  operation: string | null;
  occurrences: number;
  last_seen_at: string | null;
  next_retry_at: string | null;
  alerted_at: string | null;
  resolution_note: string | null;
}

export interface N8nStatus {
  configured: boolean;
  reachable: boolean | null;
  hint: string | null;
  workflows: { id: string; name: string; active: boolean; updated_at: string | null }[];
  executions: { id: string; workflow_name: string; status: string; mode: string; started_at: string;
                stopped_at: string | null }[];
}

export interface AgentStatus {
  gemini_configured: boolean;
  gemini_model: string;
  whatsapp_mock_mode: boolean;
  whatsapp_env: string;
  whatsapp_configured: boolean;
  webhook_signature_check: boolean;
  graph_api_version: string;
}
