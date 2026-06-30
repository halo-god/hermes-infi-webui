export interface User {
  id: string;
  email: string;
  name: string;
  handle: string | null;
  initials: string | null;
  color: string | null;
  title: string | null;
  department: string | null;
  source: string;
  role: "super_admin" | "admin" | "team_admin" | "member" | "viewer";
  status: string;
  preferences: Record<string, string> | null;
  created_at: string;
  last_active_at: string | null;
}

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface LoginResponse extends TokenPair {
  user: User;
}

export interface ProviderInfo {
  id: string;
  label: string;
  enabled: boolean;
  kind: string;
}

export type LoginMethod = "local" | "ldap" | "wecom";

// ── Admin (P4) ──
export interface AdminStats {
  users: number;
  teams: number;
  conversations: number;
  messages: number;
  agents: number;
  active_users: number;
  pending_users: number;
  role_distribution: Record<string, number>;
  source_distribution: Record<string, number>;
}

export interface AdminRole {
  id: string;
  name: string;
  desc: string;
  system: boolean;
  users: number;
}

export interface PermissionItem {
  id: string;
  name: string;
  roles: string[];
}

export interface PermissionGroup {
  group: string;
  items: PermissionItem[];
}

export interface RolesMatrix {
  roles: AdminRole[];
  permissions: PermissionGroup[];
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor_id: string | null;
  actor_name: string | null;
  action: string;
  target: string | null;
  ip: string | null;
  result: "ok" | "fail" | "partial";
  meta: Record<string, unknown>;
}

export interface SystemSettings {
  data: {
    branding: {
      tenant_name: string;
      display: string;
      short_name: string;
      login_tagline: string;
      login_subtitle: string;
      accent: string;
    };
    model_gateway: {
      default_model: string;
      monthly_token_quota: number;
      rate_limit_per_min: number;
      overage: string;
    };
  };
  updated_at: string;
}

/** Unauthenticated, front-facing branding payload (GET /branding). */
export interface BrandingPublic {
  tenant_name: string;
  display: string;
  short_name: string;
  login_tagline: string;
  login_subtitle: string;
  accent: string;
  favicon_url: string | null;
  logo_url: string | null;
}

export interface BrandAssetOut {
  kind: string;
  mime: string;
  updated_at: string;
  url: string;
}

export interface IdentityProvider {
  id: string;
  label: string;
  enabled: boolean;
  config: Record<string, string | number | boolean>;
}

export interface DeptMapping {
  id: string;
  provider_id: string;
  org_id?: string | null;
  match_basis: string;
  source_value: string;
  dept: string | null;
  default_role: string;
  auto_join_team_id: string | null;
}

/** One connected WeChat Work (企业微信) organization. Stored as config.orgs[]
 *  inside the "wecom" identity provider. */
export interface WecomOrg {
  id: string;
  name: string;
  corp_id: string;
  agent_id: string;
  app_secret: string;
  redirect_uri: string;
  silent_redirect_uri: string;
}

// ── Agents / conversations (P2) ──
export interface Agent {
  id: string;
  label: string;
  kind: string;
  available: boolean;
  official: boolean;
  version: string | null;
  color: string | null;
  icon: string | null;
  description: string | null;
}

export interface MessageContent {
  text: string;
  files?: Array<{ id: string; name: string; kind: string; diff?: string | null }>;
  [k: string]: unknown;
}

export interface PlanEntry {
  content: string;
  status: "pending" | "in_progress" | "completed";
  priority: number;
}

export interface RoundtableReply {
  agent_id: string;
  text: string;
  status: "streaming" | "complete" | "error" | "timeout";
}

export interface RoundtableContent {
  replies: RoundtableReply[];
  merged: { text: string; status: "pending" | "streaming" | "complete" | "cancelled" };
}

export interface ReplyRef {
  id: string;
  role: string;
  owner_id?: string | null;
  agent_id?: string | null;
  snippet: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  owner_id: string | null;
  role: "user" | "agent" | "roundtable" | "system";
  agent_id: string | null;
  content: MessageContent & Partial<RoundtableContent>;
  status: "streaming" | "complete" | "cancelled" | "error";
  mentions?: string[] | null;
  created_at: string;
  steps?: { title: string; status: string }[];
  thinking?: string;
  plan?: PlanEntry[];
  usage?: { input_tokens: number; output_tokens: number; context_size?: number; context_used?: number };
  reply_to_id?: string | null;
  reply_to?: ReplyRef | null;
  task_id?: string | null;
  edited_at?: string | null;
  deleted_at?: string | null;
  reactions?: Record<string, string[]>;
}

export interface Conversation {
  id: string;
  title: string;
  icon: string | null;
  type?: "personal" | "group";
  primary_agent_id: string;
  active_agent_ids: string[];
  active_profile_ids: string[];
  profile_id: string | null;
  acp_session_id: string | null;
  session_mode?: string | null;
  pinned: boolean;
  visibility: string;
  channel_mode?: string;
  team_id: string | null;
  project_id: string | null;
  project_name: string | null;
  folder_id: string | null;
  created_at: string;
  updated_at: string;
  unread?: number;
  has_mention?: boolean;
}

export interface ConversationFolder {
  id: string;
  name: string;
  sort_order: number;
  pinned: boolean;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface GroupMember {
  id: string;
  user_id: string | null;
  user_name?: string;
  profile_id: string | null;
  profile_name?: string;
  profile_icon?: string;
  profile_color?: string;
  agent_id: string | null;
  role: "admin" | "member";
  joined_at: string;
  last_read_at?: string | null;
  presence?: string | null;
}

// Generic file item usable by both WorkspacePanel and KnowledgePanel.
export interface FileItem {
  id: string;
  name: string;
  kind: string;
  size_bytes: number;
  current_version?: number;
  updated_at?: string;
}

export interface WsAdapter {
  getContent: (fileId: string) => Promise<string>;
  getRawUrl: (fileId: string) => string;
  patchContent?: (fileId: string, content: string) => Promise<string>;
  getVersions?: (fileId: string) => Promise<WorkspaceFileVersion[]>;
  restoreVersion?: (fileId: string, versionNum: number) => Promise<string>;
  upload?: (file: File) => Promise<void>;
}

export interface WorkspaceFile {
  id: string;
  conversation_id: string;
  name: string;
  kind: string;
  current_version: number;
  size_bytes: number;
  created_by_agent: string | null;
  updated_at: string;
}

export interface WorkspaceFileVersion {
  id: string;
  file_id: string;
  version_num: number;
  content?: string;
  size_bytes: number;
  created_at: string;
  author: string | null;
}

export interface ConfirmationRequest {
  id: string;
  conversation_id: string;
  message_id: string;
  question: string;
  options: string[];
  // Multi-question mode: each sub-question has its own options
  questions?: Array<{ question: string; options: string[]; allow_free_text?: boolean }>;
}

// Clarify Q&A persisted in message content (audit trail + modal restore on reload)
export interface ClarifyEntry {
  id: string;
  question: string;
  options: string[];
  status: "pending" | "answered" | "auto" | "timeout" | "cancelled";
  choice?: string;
  ts?: string;
}

export interface RtAgentMeta {
  agent_id: string;
  slot: number;
  label: string;
  color: string;
  stance: string;
}

// Event frames from the agent runner (SSE single-agent + WS roundtable).
// `conversation_id` is injected centrally by the backend so handlers can drop
// events that belong to another conversation (switch-while-streaming).
export type StreamEvent = (
  | { type: "start"; message_id: string }
  | { type: "token"; message_id: string; delta: string }
  | { type: "tool_call"; message_id: string; title?: string; status?: string }
  | { type: "file"; message_id: string; file_id: string; name: string; kind: string; version: number; diff?: string | null }
  | { type: "done"; message_id: string; status: string; stop_reason?: string; text?: string }
  | { type: "error"; message_id: string; detail: string }
  | { type: "rt_start"; message_id: string; agents: RtAgentMeta[] }
  | { type: "rt_token"; message_id: string; slot: number; delta: string }
  | { type: "rt_reply_done"; message_id: string; slot: number; status?: RoundtableReply["status"] }
  | { type: "merge_start"; message_id: string }
  | { type: "merge_token"; message_id: string; delta: string }
  | { type: "confirmation_request"; message_id: string; request: ConfirmationRequest }
  | { type: "confirmation_response"; message_id: string; request_id: string; choice: string }
  | { type: "clarify_auto"; message_id: string; question: string; choice: string }
  | { type: "thought"; message_id: string; delta: string }
  | { type: "plan"; message_id: string; entries: PlanEntry[] }
  | { type: "usage"; message_id: string; input_tokens?: number; output_tokens?: number; context_size?: number; context_used?: number }
  | { type: "session_info"; title?: string }
  | { type: "message"; message: Message }
  | { type: "message_update"; message_id: string; patch: Partial<Message> }
  | { type: "typing"; user_id: string; name?: string }
  | { type: "members_changed" }
  | { type: "notify"; title?: string; snippet?: string; mention?: boolean; unread?: number }
) & { conversation_id?: string };

// ── Teams / projects / tasks (P3 backend; frontend added here) ──
export interface Team {
  id: string;
  name: string;
  handle: string | null;
  tagline: string | null;
  color: string | null;
  plan: string;
  join_mode: string;
  created_at: string;
}

export interface Member {
  user_id: string;
  role: string;
  status: string;
  joined_at: string;
  name: string | null;
  email: string | null;
  initials: string | null;
  color: string | null;
}

export interface Knowledge {
  id: string;
  name: string;
  kind: string;
  size_bytes: number;
  uploaded_by_name: string | null;
  folder_id: string | null;
  is_folder: boolean;
  sort_order: number;
  created_at?: string;
}
export interface ActivityItem {
  who: string;
  action: string;
  target: string;
  icon: string;
  ago: string;
}
export interface ConversationBrief {
  id: string;
  title: string;
  primary_agent_id: string;
  updated_at: string;
}
export interface TeamStats {
  members: number;
  agents: number;
  threads: number;
  knowledge: number;
}

export interface TeamDetail extends Team {
  my_role: string;
  members: Member[];
  shared_profile_ids: string[];
  stats: TeamStats;
  knowledge: Knowledge[];
  activity: ActivityItem[];
  pinned: ConversationBrief[];
}

export interface PermissionItem {
  id: string;
  group: string;
  label: string;
}
export interface PermissionGroup {
  group: string;
  permissions: PermissionItem[];
}
export interface TeamPolicy {
  my_role: string;
  editable: boolean;
  permissions: PermissionGroup[];
  policy: Record<string, Record<string, boolean>>;
}

export interface Project {
  id: string;
  team_id: string;
  name: string;
  handle: string | null;
  color: string | null;
  icon: string | null;
  summary: string | null;
  progress: number;
  status: string;
  sections: string[];
  pinned_profile_ids: string[];
  member_ids: string[];
  visibility: string;
  deadline: string | null;
  created_at: string;
}

export interface ProjectDoc {
  id: string;
  name: string;
  kind: string;
  size_bytes: number;
  created_by_name: string | null;
  folder_id: string | null;
  is_folder: boolean;
  created_at: string;
}

export interface ProjectActivity {
  id: string;
  project_id: string;
  actor_id: string | null;
  actor_name: string | null;
  kind: string;
  summary: string;
  meta: Record<string, unknown>;
  created_at: string;
}
export interface ProjectDetail extends Project {
  members: Member[];
  docs: ProjectDoc[];
  conversations: ConversationBrief[];
}

export interface Task {
  id: string;
  project_id: string;
  title: string;
  status: "todo" | "doing" | "done" | string;
  owner_id: string | null;
  agent_id: string | null;
  order_idx: number;
  description?: string | null;
  source_conversation_id?: string | null;
  source_message_id?: string | null;
  created_at: string;
}

export interface ScheduledTask {
  id: string;
  owner_id: string;
  name: string;
  agent_id: string;
  prompt: string;
  cron: string;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string | null;
  last_status: string | null;
  created_at: string;
  updated_at: string;
}

export interface Feedback {
  id: number;
  user_id: string;
  user_name: string;
  title: string;
  content: string;
  category: string;
  status: string;
  priority: string;
  reply: string | null;
  replied_by: string | null;
  replied_at: string | null;
  images: string[];
  created_at: string;
  updated_at: string;
}
