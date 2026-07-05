<script setup lang="ts">
/* Faithful 1:1 re-port of the prototype admin console (hermes-admin*.js) — admin
   hero + tabs, overview dashboard (stats + role distribution + identity + recent
   activity), users table, RBAC role cards + permission matrix, identity providers
   with rich detail panel, audit log with filters, system settings + danger zone.
   Wired to the real /admin API. */
import { computed, onMounted, onUnmounted, reactive, ref, watch } from "vue";
import Icon from "@/components/Icon.vue";
import { adminApi } from "@/api/admin";
import { brandingApi } from "@/api/branding";
import { http } from "@/api/client";
import { fmtDate } from "@/utils/format";
import { agentsApi, profilesApi, type Profile, type ProfileCreate } from "@/api/agents";
import { teamsApi } from "@/api/teams";
import { useAuthStore } from "@/stores/auth";
import { useBrandingStore } from "@/stores/branding";
import { usePresence } from "@/composables/usePresence";
import type {
  AdminRole,
  AdminStats,
  AuditEntry,
  DeptMapping,
  IdentityProvider,
  PermissionGroup,
  SystemSettings,
  User,
  WecomOrg,
} from "@/types";

const authStore = useAuthStore();
const branding = useBrandingStore();
const isSuperAdmin = computed(() => authStore.user?.role === "super_admin");
const { queryPresence, getStatus } = usePresence();

const tab = ref<"overview" | "users" | "roles" | "identity" | "audit" | "assistants" | "system" | "mcp" | "performance">("overview");
const stats = ref<AdminStats | null>(null);
const users = ref<User[]>([]);
const userQ = ref("");
const sourceFilter = ref<"all" | "local" | "ldap" | "wecom">("all");
const statusFilter = ref<"all" | "active" | "pending" | "inactive">("all");
const audit = ref<AuditEntry[]>([]);
const auditQ = ref("");
const resultFilter = ref<"all" | "ok" | "fail" | "partial">("all");
const auditLimit = ref(100);
const auditAutoRefresh = ref(false);
const auditDateFrom = ref("");
const auditDateTo = ref("");
let _auditTimer: ReturnType<typeof setInterval> | null = null;
const settings = ref<SystemSettings["data"] | null>(null);
const savingSettings = ref(false);
const assetBusy = ref<"favicon" | "logo" | null>(null);
const providers = ref<IdentityProvider[]>([]);
const activeProvider = ref<string>("ldap");
const ldap = ref<IdentityProvider | null>(null);
const wecom = ref<IdentityProvider | null>(null);
const mappings = ref<DeptMapping[]>([]);
const teamsOpt = ref<{ id: string; name: string }[]>([]);
const roles = ref<AdminRole[]>([]);
const permissions = ref<PermissionGroup[]>([]);
const newMap = reactive({ source_value: "", default_role: "member", auto_join_team_id: "" });

// ── WeCom multi-org state ──
// Editable org list (mirrors wecom.config.orgs); mappings + test results keyed by org id.
const wecomOrgs = ref<WecomOrg[]>([]);
const wecomMappingsByOrg = reactive<Record<string, DeptMapping[]>>({});
const wecomMapDrafts = reactive<Record<string, { source_value: string; default_role: string; auto_join_team_id: string }>>({});
const wecomTestByOrg = reactive<Record<string, { ok: boolean; message: string } | null>>({});
const wecomTestingOrg = ref<string | null>(null);
const wecomSecretShown = reactive<Record<string, boolean>>({});

function blankWecomOrg(id: string, name = ""): WecomOrg {
  return { id, name, corp_id: "", agent_id: "", app_secret: "", redirect_uri: "", silent_redirect_uri: "" };
}
function wecomMapDraft(orgId: string) {
  if (!wecomMapDrafts[orgId]) wecomMapDrafts[orgId] = { source_value: "", default_role: "member", auto_join_team_id: "" };
  return wecomMapDrafts[orgId];
}

const LDAP_DEFAULTS: Record<string, string | number | boolean> = {
  host: "", port: 389, use_ssl: false, start_tls: false,
  auth_mode: "direct",
  user_dn_template: "",
  bind_dn: "", bind_password: "", base_dn: "", search_filter: "(uid={username})",
  attr_email: "mail", attr_name: "cn", attr_dept: "departmentNumber", email_domain: "",
};
const ldapShowPw = ref(false);
const ldapTesting = ref(false);
const ldapSaving = ref(false);
const ldapTestResult = ref<{ ok: boolean; message: string } | null>(null);

const wecomSaving = ref(false);

const ROLES = ["super_admin", "admin", "team_admin", "member", "viewer"];
const ROLE_NAME: Record<string, string> = { super_admin: "超级管理员", admin: "管理员", team_admin: "团队管理员", member: "成员", viewer: "只读" };
const STATUS_LABEL: Record<string, string> = { active: "激活", pending: "待激活", inactive: "已停用" };
const SOURCE_LABEL: Record<string, string> = { local: "本地", ldap: "LDAP", wecom: "企业微信", saml: "SAML", oidc: "OIDC" };
const ROLE_COLOR: Record<string, string> = { super_admin: "#1d1a14", admin: "#b8852a", team_admin: "#3a6da1", member: "#3a8a7a", viewer: "#8a8474" };

const newUser = reactive({ email: "", name: "", password: "", role: "member", department: "" });
const creating = ref(false);
const createError = ref("");
const showCreate = ref(false);

// ── MCP Servers ──
interface McpServer { name: string; transport: string; command: string | null; url: string | null; env: Record<string, string> | null }
const mcpServers = ref<McpServer[]>([]);
const mcpStatuses = ref<Record<string, { reachable: boolean; status: string }>>({});
const mcpLoading = ref(false);
const mcpForm = reactive({ name: "", transport: "stdio", command: "", url: "", envStr: "" });
const mcpSaving = ref(false);
const mcpError = ref("");
const showMcpForm = ref(false);

async function loadMcpServers() {
  mcpLoading.value = true;
  try { mcpServers.value = (await http.get("/admin/mcp-servers")).data; } catch { mcpServers.value = []; } finally { mcpLoading.value = false; }
}

// ── Performance ──
const perfData = ref<Record<string, unknown> | null>(null);
const perfLoading = ref(false);
async function loadPerformance() {
  perfLoading.value = true;
  try { perfData.value = (await http.get("/admin/performance")).data; } catch { perfData.value = null; } finally { perfLoading.value = false; }
}
async function checkMcpStatus(name: string) {
  try {
    const r = await http.get(`/admin/mcp-servers/${name}/status`);
    return r.data;
  } catch { return { name, status: "error", reachable: false }; }
}
async function addMcpServer() {
  mcpSaving.value = true; mcpError.value = "";
  try {
    let env: Record<string, string> | null = null;
    if (mcpForm.envStr.trim()) {
      env = {};
      for (const line of mcpForm.envStr.split("\n")) {
        const [k, ...v] = line.split("=");
        if (k.trim()) env[k.trim()] = v.join("=").trim();
      }
    }
    await http.post("/admin/mcp-servers", {
      name: mcpForm.name,
      transport: mcpForm.transport,
      command: mcpForm.transport === "stdio" ? mcpForm.command || null : null,
      url: mcpForm.transport === "http" ? mcpForm.url || null : null,
      env,
    });
    showMcpForm.value = false;
    Object.assign(mcpForm, { name: "", transport: "stdio", command: "", url: "", envStr: "" });
    await loadMcpServers();
  } catch (e: any) {
    mcpError.value = e?.response?.data?.detail || "添加失败";
  } finally {
    mcpSaving.value = false;
  }
}
async function deleteMcpServer(name: string) {
  await http.delete(`/admin/mcp-servers/${encodeURIComponent(name)}`);
  await loadMcpServers();
}
watch(tab, (t) => {
  if (t === "mcp") loadMcpServers();
  if (t === "performance") loadPerformance();
});

onMounted(load);
onUnmounted(() => {
  if (_auditTimer) { clearInterval(_auditTimer); _auditTimer = null; }
});
async function load() {
  try { stats.value = await adminApi.stats(); } catch { /* will show nulls */ }
  await Promise.allSettled([loadUsers(), loadAudit(), loadSettings(), loadIdentity(), loadRoles(), loadProfiles()]);
}
async function loadUsers() {
  users.value = await adminApi.listUsers(userQ.value || undefined);
  // Query online/offline presence for all users
  const userIds = users.value.map((u) => u.id).filter(Boolean);
  if (userIds.length) queryPresence(userIds);
}
async function loadAudit() {
  audit.value = await adminApi.audit({
    result: resultFilter.value !== "all" ? resultFilter.value : undefined,
    limit: auditLimit.value,
    date_from: auditDateFrom.value || undefined,
    date_to: auditDateTo.value || undefined,
  });
}
function toggleAutoRefresh() {
  auditAutoRefresh.value = !auditAutoRefresh.value;
  if (_auditTimer) { clearInterval(_auditTimer); _auditTimer = null; }
  if (auditAutoRefresh.value) _auditTimer = setInterval(loadAudit, 15000);
}
function loadMore() {
  auditLimit.value += 100;
  loadAudit();
}
async function loadSettings() {
  const raw = (await adminApi.getSettings()).data ?? {} as Record<string, unknown>;
  settings.value = {
    branding: { tenant_name: "", display: "", short_name: "", login_tagline: "", login_subtitle: "", accent: "#b8852a", ...((raw.branding as Record<string, unknown>) || {}) },
    model_gateway: { default_model: "claude-sonnet-4-6", monthly_token_quota: 1000000, rate_limit_per_min: 20, overage: "soft", ...((raw.model_gateway as Record<string, unknown>) || {}) },
  };
}
async function loadRoles() {
  const m = await adminApi.roles();
  roles.value = m.roles;
  permissions.value = m.permissions;
}

const permTogglingKey = ref<string | null>(null);

async function togglePermission(permId: string, roleId: string, currentlyGranted: boolean) {
  if (!isSuperAdmin.value) return;
  const key = `${permId}:${roleId}`;
  permTogglingKey.value = key;
  try {
    await adminApi.togglePermission(permId, roleId, !currentlyGranted);
    await loadRoles();
  } finally {
    permTogglingKey.value = null;
  }
}
async function loadIdentity() {
  providers.value = await adminApi.identity();
  ldap.value = providers.value.find((p) => p.id === "ldap") || null;
  if (ldap.value) {
    for (const [k, v] of Object.entries(LDAP_DEFAULTS))
      if (ldap.value.config[k] === undefined) ldap.value.config[k] = v;
    mappings.value = await adminApi.mappings("ldap");
  }
  wecom.value = providers.value.find((p) => p.id === "wecom") || null;
  if (wecom.value) {
    wecomOrgs.value = normalizeWecomOrgs(wecom.value.config);
    for (const o of wecomOrgs.value) {
      wecomMappingsByOrg[o.id] = await adminApi.mappings("wecom", o.id);
    }
  }
  try {
    teamsOpt.value = (await http.get("/teams")).data;
  } catch {
    teamsOpt.value = [];
  }
}

const filteredUsers = () =>
  users.value.filter((u) => {
    if (sourceFilter.value !== "all" && u.source !== sourceFilter.value) return false;
    if (statusFilter.value !== "all" && u.status !== statusFilter.value) return false;
    return true;
  });
const filteredAudit = computed(() => {
  const term = auditQ.value.trim().toLowerCase();
  return audit.value.filter((a) => {
    if (resultFilter.value !== "all" && a.result !== resultFilter.value) return false;
    if (!term) return true;
    return [a.actor_name, a.action, a.target, a.ip].some((v) => (v || "").toLowerCase().includes(term));
  });
});
function nextOf<T>(arr: T[], cur: T): T {
  return arr[(arr.indexOf(cur) + 1) % arr.length];
}

// ── overview derived ──
const roleDist = computed(() =>
  roles.value.map((r) => ({ ...r, pct: stats.value?.users ? Math.round((r.users / stats.value.users) * 100) : 0 })),
);
const userCap = computed(() => Math.max(200, Math.ceil((stats.value?.users || 0) / 50) * 50));
const sourceBreakdown = computed(() => {
  const d = stats.value?.source_distribution || {};
  const local = d.local || 0;
  const sso = (stats.value?.users || 0) - local;
  return `本地 ${local} · SSO ${sso}`;
});

async function changeRole(u: User, role: string) {
  await adminApi.updateUser(u.id, { role });
  u.role = role as User["role"];
  await Promise.all([loadAudit(), loadRoles(), refreshStats()]);
}
async function toggleStatus(u: User) {
  const next = u.status === "active" ? "inactive" : "active";
  const updated = await adminApi.updateUser(u.id, { status: next });
  u.status = updated.status;
  await Promise.all([loadAudit(), refreshStats()]);
}
async function refreshStats() {
  stats.value = await adminApi.stats();
}
async function createUser() {
  createError.value = "";
  creating.value = true;
  try {
    await adminApi.createUser({ ...newUser });
    Object.assign(newUser, { email: "", name: "", password: "", role: "member", department: "" });
    showCreate.value = false;
    await Promise.all([loadUsers(), loadAudit(), loadRoles(), refreshStats()]);
  } catch (e: unknown) {
    createError.value = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail || "创建失败";
  } finally {
    creating.value = false;
  }
}
async function saveSettings() {
  if (!settings.value) return;
  savingSettings.value = true;
  try {
    settings.value = (await adminApi.putSettings(settings.value)).data;
    await loadAudit();
    // Reflect text changes across the app immediately.
    await branding.fetchBranding();
  } finally {
    savingSettings.value = false;
  }
}

/** Live favicon/logo URLs shown in the admin form (from the public branding endpoint). */
const liveBranding = computed(() => branding.branding);

async function onAssetPicked(kind: "favicon" | "logo", e: Event) {
  const input = e.target as HTMLInputElement;
  const file = input.files?.[0];
  if (!file) return;
  assetBusy.value = kind;
  try {
    await brandingApi.uploadAsset(kind, file);
    await branding.fetchBranding();
    await loadAudit();
  } finally {
    assetBusy.value = null;
    input.value = "";
  }
}

async function onAssetClear(kind: "favicon" | "logo") {
  assetBusy.value = kind;
  try {
    await brandingApi.deleteAsset(kind);
    await branding.fetchBranding();
    await loadAudit();
  } finally {
    assetBusy.value = null;
  }
}
async function toggleProvider(p: IdentityProvider) {
  const u = await adminApi.updateProvider(p.id, { enabled: !p.enabled });
  p.enabled = u.enabled;
  await loadAudit();
}
async function saveLdap() {
  if (!ldap.value) return;
  ldapSaving.value = true;
  try {
    await adminApi.updateProvider("ldap", { config: ldap.value.config });
    await loadAudit();
  } finally {
    ldapSaving.value = false;
  }
}
async function testLdap() {
  ldapTesting.value = true;
  ldapTestResult.value = null;
  try {
    ldapTestResult.value = await adminApi.testProvider("ldap");
  } catch {
    ldapTestResult.value = { ok: false, message: "请求失败，请检查服务端日志" };
  } finally {
    ldapTesting.value = false;
  }
}
/** Normalize a wecom provider config into an editable org list, mirroring the
 *  backend's wecom.normalize_orgs (supports the legacy single-org config). */
function normalizeWecomOrgs(config: Record<string, string | number | boolean>): WecomOrg[] {
  const raw = (config as { orgs?: unknown }).orgs;
  if (Array.isArray(raw) && raw.length) {
    return raw.map((o, i) => {
      const oo = (o || {}) as Partial<WecomOrg>;
      return {
        id: (oo.id || "").trim() || (i === 0 ? "default" : `org${i}`),
        name: (oo.name || "").trim() || "企业微信",
        corp_id: oo.corp_id || "", agent_id: oo.agent_id || "", app_secret: oo.app_secret || "",
        redirect_uri: oo.redirect_uri || "", silent_redirect_uri: oo.silent_redirect_uri || "",
      };
    });
  }
  if (String(config.corp_id || "").trim()) {
    return [{
      id: "default", name: "企业微信",
      corp_id: String(config.corp_id || ""), agent_id: String(config.agent_id || ""),
      app_secret: String(config.app_secret || ""), redirect_uri: String(config.redirect_uri || ""),
      silent_redirect_uri: String(config.silent_redirect_uri || ""),
    }];
  }
  // No org yet — start with one blank default org so the form has something to edit.
  return [blankWecomOrg("default")];
}
async function saveWecom() {
  if (!wecom.value) return;
  wecomSaving.value = true;
  try {
    await adminApi.updateProvider("wecom", { config: { orgs: wecomOrgs.value } });
    await loadIdentity();
    await loadAudit();
  } finally {
    wecomSaving.value = false;
  }
}
function addWecomOrg() {
  const id = `org_${Math.random().toString(36).slice(2, 8)}`;
  wecomOrgs.value.push(blankWecomOrg(id, "新组织"));
}
async function removeWecomOrg(org: WecomOrg) {
  if (!confirm(`确定删除组织「${org.name}」及其登录配置？`)) return;
  wecomOrgs.value = wecomOrgs.value.filter((o) => o.id !== org.id);
  delete wecomMappingsByOrg[org.id];
  delete wecomTestByOrg[org.id];
  await saveWecom();
}
async function testWecomOrg(org: WecomOrg) {
  wecomTestingOrg.value = org.id;
  wecomTestByOrg[org.id] = null;
  try {
    // Persist first so the backend tests the latest credentials.
    await adminApi.updateProvider("wecom", { config: { orgs: wecomOrgs.value } });
    wecomTestByOrg[org.id] = await adminApi.testProvider("wecom", org.id);
  } catch {
    wecomTestByOrg[org.id] = { ok: false, message: "请求失败，请检查服务端日志" };
  } finally {
    wecomTestingOrg.value = null;
  }
}
async function addMapping() {
  if (!newMap.source_value.trim()) return;
  const m = await adminApi.addMapping("ldap", { match_basis: "attribute", source_value: newMap.source_value.trim(), default_role: newMap.default_role, auto_join_team_id: newMap.auto_join_team_id || null });
  mappings.value.push(m);
  newMap.source_value = "";
  newMap.auto_join_team_id = "";
}
async function addWecomMapping(org: WecomOrg) {
  const draft = wecomMapDraft(org.id);
  if (!draft.source_value.trim()) return;
  const m = await adminApi.addMapping("wecom", { org_id: org.id, match_basis: "attribute", source_value: draft.source_value.trim(), default_role: draft.default_role, auto_join_team_id: draft.auto_join_team_id || null });
  (wecomMappingsByOrg[org.id] ||= []).push(m);
  draft.source_value = "";
  draft.auto_join_team_id = "";
}
async function deleteMapping(id: string) {
  await adminApi.deleteMapping(id);
  mappings.value = mappings.value.filter((m) => m.id !== id);
}
async function deleteWecomMapping(orgId: string, id: string) {
  await adminApi.deleteMapping(id);
  wecomMappingsByOrg[orgId] = (wecomMappingsByOrg[orgId] || []).filter((m) => m.id !== id);
}

function providerLetter(k: string) {
  return ({ ldap: "L", wecom: "微", saml: "S", oidc: "O", feishu: "飞" } as Record<string, string>)[k] || "?";
}
function providerColor(k: string) {
  return ({ ldap: "#3a6da1", wecom: "#3a7a2a", saml: "#6a3aa1", oidc: "#c47a2a", feishu: "#3a8a8a" } as Record<string, string>)[k] || "#b8852a";
}
function providerSubtitle(id: string) {
  return ({
    ldap: "通过 LDAP / Active Directory 协议同步域内用户，作为权威身份源。",
    wecom: "企业微信扫码登录、成员自动开通、部门→团队映射。",
    saml: "SAML 2.0 单点登录（Okta、OneLogin、ADFS 等）",
    oidc: "基于 OpenID Connect 的标准化登录（Auth0、Azure AD 等）",
    feishu: "飞书企业版扫码登录与组织架构同步",
  } as Record<string, string>)[id] || "";
}
const activeProviderObj = computed(() => providers.value.find((p) => p.id === activeProvider.value) || null);
function teamName(id: string | null) {
  return teamsOpt.value.find((t) => t.id === id)?.name || (id ? "未知团队" : "不自动加入");
}

const providersOn = computed(() => providers.value.filter((p) => p.enabled).length);

const TABS = [
  ["overview", "概览"], ["users", "用户管理"], ["roles", "权限管理"],
  ["identity", "身份与连接"], ["audit", "审计日志"], ["assistants", "助手管理"], ["mcp", "MCP 服务器"], ["system", "系统设置"], ["performance", "性能监控"],
] as const;

// ── Assistants (Profiles) ──
const profiles = ref<Profile[]>([]);
const scanLoading = ref(false);
const scanMsg = ref("");
const hermesVersion = ref("");
const showProfileForm = ref(false);
const editingProfileId = ref<string | null>(null);
const profileForm = reactive<ProfileCreate & {
  knowledge_ids: string[]; mcp_server_names: string[]; is_moa: boolean; moa_target_profile_ids: string[];
}>({
  name: "", handle: "", scope: "global", color: "#b8852a",
  icon: "sparkle", desc: "", default_model: "hermes-4", team_id: null,
  system_prompt: null, skills: [], featured: false, knowledge_ids: [], mcp_server_names: [],
  is_moa: false, moa_target_profile_ids: [],
});
// Selectable knowledge entries (team knowledge, labeled by team) for prompt injection.
const knowledgeOptions = ref<{ id: string; label: string }[]>([]);
async function loadKnowledgeOptions() {
  const opts: { id: string; label: string }[] = [];
  for (const t of teamsOpt.value) {
    try {
      const ks = await teamsApi.listKnowledge(t.id);
      for (const k of ks) opts.push({ id: k.id, label: `${t.name} · ${k.name}` });
    } catch { /* skip teams without access */ }
  }
  knowledgeOptions.value = opts;
}
function toggleKnowledge(id: string) {
  const arr = profileForm.knowledge_ids;
  const i = arr.indexOf(id);
  if (i >= 0) arr.splice(i, 1);
  else arr.push(id);
}
function toggleMcpServer(name: string) {
  const arr = profileForm.mcp_server_names;
  const i = arr.indexOf(name);
  if (i >= 0) arr.splice(i, 1);
  else arr.push(name);
}
function toggleMoaTarget(id: string) {
  const arr = profileForm.moa_target_profile_ids;
  const i = arr.indexOf(id);
  if (i >= 0) arr.splice(i, 1);
  else arr.push(id);
}
const skillInput = ref("");
const profileSaving = ref(false);
const profileError = ref("");
const SCOPE_LABEL: Record<string, string> = { personal: "个人", team: "团队", global: "全局" };

async function loadProfiles() {
  profiles.value = await profilesApi.list();
}
const scanErrors = ref<string[]>([]);
const scanHermesPath = ref<string | null>(null);
const scanProfilesLoading = ref(false);
const scanProfilesMsg = ref("");
const scanProfilesErrors = ref<string[]>([]);

async function scanAgents() {
  scanLoading.value = true;
  scanMsg.value = "";
  scanErrors.value = [];
  scanHermesPath.value = null;
  try {
    const result = await agentsApi.scanAgents();
    hermesVersion.value = result.version && result.version !== "unknown" ? result.version : "";
    scanMsg.value = `发现 ${result.found} 个 ACP Agent，新增 ${result.created}，更新 ${result.updated}`;
    scanErrors.value = result.errors || [];
    scanHermesPath.value = result.hermes_path;
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
    scanMsg.value = "扫描失败";
    scanErrors.value = [detail || "服务器返回错误，请检查后端日志"];
  } finally {
    scanLoading.value = false;
  }
}

async function scanProfilesFn() {
  scanProfilesLoading.value = true;
  scanProfilesMsg.value = "";
  scanProfilesErrors.value = [];
  try {
    const result = await profilesApi.scan();
    scanProfilesMsg.value = result.message || `新增 ${result.created} 个助手，发现 ${result.profiles_found} 个 Profile`;
    scanProfilesErrors.value = result.errors || [];
    await loadProfiles();
  } catch (e: unknown) {
    const detail = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail;
    scanProfilesMsg.value = "扫描失败";
    scanProfilesErrors.value = [detail || "服务器返回错误，请检查后端日志"];
  } finally {
    scanProfilesLoading.value = false;
  }
}
function openCreateProfile() {
  editingProfileId.value = null;
  Object.assign(profileForm, {
    name: "", handle: "", scope: "global", color: "#b8852a",
    icon: "sparkle", desc: "", default_model: "hermes-4", team_id: null,
    system_prompt: null, skills: [], featured: false, knowledge_ids: [], mcp_server_names: [],
    is_moa: false, moa_target_profile_ids: [],
  });
  skillInput.value = "";
  profileError.value = "";
  showProfileForm.value = true;
  loadKnowledgeOptions();
  loadMcpServers();
}
function openEditProfile(p: Profile) {
  editingProfileId.value = p.id;
  Object.assign(profileForm, {
    name: p.name, handle: p.handle, scope: p.scope, color: p.color,
    icon: p.icon, desc: p.desc, default_model: p.default_model, team_id: p.team_id,
    system_prompt: p.system_prompt ?? null, skills: [...(p.skills || [])], featured: p.featured ?? false,
    knowledge_ids: [...(p.knowledge_ids || [])], mcp_server_names: [...(p.mcp_server_names || [])],
    is_moa: p.is_moa ?? false, moa_target_profile_ids: [...(p.moa_target_profile_ids || [])],
  });
  skillInput.value = "";
  profileError.value = "";
  showProfileForm.value = true;
  loadKnowledgeOptions();
  loadMcpServers();
}
function addSkill() {
  const s = skillInput.value.trim();
  if (s && !profileForm.skills!.includes(s)) {
    profileForm.skills!.push(s);
  }
  skillInput.value = "";
}
function removeSkill(s: string) {
  profileForm.skills = profileForm.skills!.filter((x) => x !== s);
}
async function saveProfile() {
  profileSaving.value = true;
  profileError.value = "";
  try {
    if (editingProfileId.value) {
      await profilesApi.update(editingProfileId.value, {
        name: profileForm.name, scope: profileForm.scope, color: profileForm.color,
        icon: profileForm.icon, desc: profileForm.desc, default_model: profileForm.default_model,
        team_id: profileForm.team_id, system_prompt: profileForm.system_prompt,
        skills: profileForm.skills, featured: profileForm.featured,
        knowledge_ids: profileForm.knowledge_ids,
        mcp_server_names: profileForm.mcp_server_names,
        is_moa: profileForm.is_moa, moa_target_profile_ids: profileForm.moa_target_profile_ids,
      });
    } else {
      await profilesApi.create({ ...profileForm });
    }
    showProfileForm.value = false;
    await loadProfiles();
  } catch (e: unknown) {
    profileError.value = (e as { response?: { data?: { detail?: string } } }).response?.data?.detail || "保存失败";
  } finally {
    profileSaving.value = false;
  }
}
async function deleteProfileItem(p: Profile) {
  if (!confirm(`删除助手「${p.name}」？此操作不可恢复。`)) return;
  await profilesApi.remove(p.id);
  await loadProfiles();
}

async function cloneProfile(p: Profile) {
  try {
    await profilesApi.clone(p.id);
    await loadProfiles();
  } catch { /* noop */ }
}

async function exportProfile(p: Profile) {
  try {
    const data = await profilesApi.export(p.id);
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `profile-${p.handle}.json`;
    a.click();
    URL.revokeObjectURL(url);
  } catch { /* noop */ }
}

async function exportAllProfiles() {
  try {
    const all = await Promise.all(profiles.value.map((p) => profilesApi.export(p.id)));
    const blob = new Blob([JSON.stringify(all, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "profiles-export.json";
    a.click();
    URL.revokeObjectURL(url);
  } catch { /* noop */ }
}

const importFileRef = ref<HTMLInputElement | null>(null);
const importPreview = ref<{ items: Record<string, string>[]; visible: boolean }>({ items: [], visible: false });

async function handleImportFile(e: Event) {
  const file = (e.target as HTMLInputElement).files?.[0];
  if (!file) return;
  try {
    const text = await file.text();
    const parsed = JSON.parse(text);
    const items = Array.isArray(parsed) ? parsed : [parsed];
    // Show preview instead of importing directly
    importPreview.value = { items, visible: true };
  } catch {
    alert("导入失败：文件格式不正确");
  }
  // Reset input
  if (importFileRef.value) importFileRef.value.value = "";
}

async function confirmImport() {
  try {
    await profilesApi.import(importPreview.value.items);
    await loadProfiles();
  } catch {
    alert("导入失败");
  }
  importPreview.value = { items: [], visible: false };
}
</script>

<template>
  <div class="stage">
    <div class="admin-hero">
      <div class="admin-hero-row">
        <span class="admin-badge"><Icon name="settings" :size="11" /> ADMIN CONSOLE</span>
        <span style="font-size: 11.5px; color: var(--ink-mute); font-family: var(--font-mono)">{{ settings?.branding?.tenant_name || branding.tenantName }}</span>
      </div>
      <h1 class="admin-title">后台<em>管理</em></h1>
      <div class="admin-sub">用户、权限、连接器与日志——管理整个 {{ branding.shortName }} 实例。</div>
      <div class="admin-tabs">
        <button v-for="t in TABS" :key="t[0]" class="team-tab" :class="{ active: tab === t[0] }" @click="tab = t[0]">{{ t[1] }}</button>
      </div>
    </div>

    <div class="admin-body">
      <!-- ───────────── OVERVIEW ───────────── -->
      <template v-if="tab === 'overview' && stats">
        <div class="stat-grid" style="grid-template-columns: repeat(4, 1fr)">
          <div class="stat">
            <div class="stat-label"><Icon name="user" /> 总用户</div>
            <div class="stat-value">{{ stats.users }}</div>
            <div class="stat-foot"><em>{{ sourceBreakdown }}</em></div>
            <div style="margin-top: 8px; height: 4px; background: var(--rule); border-radius: 2px; overflow: hidden">
              <div :style="{ width: (stats.users / userCap) * 100 + '%', height: '100%', background: 'var(--accent)' }"></div>
            </div>
            <div style="margin-top: 5px; font-size: 11px; color: var(--ink-mute)">已用 {{ stats.users }} / {{ userCap }} 席位</div>
          </div>
          <div class="stat">
            <div class="stat-label"><Icon name="bolt" /> 当前激活</div>
            <div class="stat-value">{{ stats.active_users }}</div>
            <div class="stat-foot"><em>待激活 {{ stats.pending_users }}</em></div>
            <div class="text-mute-xs" style="margin-top:13px">状态为「激活」的账号</div>
          </div>
          <div class="stat">
            <div class="stat-label"><Icon name="chat" /> 累计会话</div>
            <div class="stat-value">{{ stats.conversations }}</div>
            <div class="stat-foot"><em>{{ stats.messages }} 条消息</em></div>
            <div class="text-mute-xs" style="margin-top:13px">所有团队的累计会话</div>
          </div>
          <div class="stat">
            <div class="stat-label"><Icon name="sparkle" /> 助手 & 团队</div>
            <div class="stat-value">{{ stats.agents }}</div>
            <div class="stat-foot"><em>{{ stats.teams }} 个团队</em></div>
            <div class="text-mute-xs" style="margin-top:13px">ACP 注册的助手数量</div>
          </div>
        </div>

        <div class="col-grid" style="margin-top: 22px">
          <div class="section-card">
            <div class="section-head">
              <div class="section-title"><Icon name="globe" /> 身份与连接器</div>
              <span class="section-link">{{ providersOn }} 已启用</span>
            </div>
            <div style="padding: 8px">
              <div v-for="p in providers" :key="p.id" class="row-item" style="cursor: pointer" @click="tab = 'identity'; activeProvider = p.id">
                <div class="ip-logo" :style="{ background: providerColor(p.id), width: '28px', height: '28px', borderRadius: '7px' }">
                  <span style="font-family: var(--font-serif); font-weight: 700; font-size: 13px">{{ providerLetter(p.id) }}</span>
                </div>
                <div class="row-text">
                  <div class="row-title">{{ p.label }}</div>
                  <div class="row-sub">{{ providerSubtitle(p.id).slice(0, 28) }}…</div>
                </div>
                <span class="ip-status" :class="{ off: !p.enabled }"><span class="dot"></span>{{ p.enabled ? "已启用" : "未启用" }}</span>
              </div>
            </div>
          </div>

          <div class="flex-col-gap">
            <div class="section-card">
              <div class="section-head"><div class="section-title"><Icon name="user" /> 角色分布</div></div>
              <div style="padding: 14px 18px">
                <div v-for="r in roleDist" :key="r.id" style="margin-bottom: 10px">
                  <div style="display: flex; justify-content: space-between; font-size: 12.5px; margin-bottom: 4px">
                    <span style="color: var(--ink); font-weight: 500">{{ r.name }}</span>
                    <span class="text-mute">{{ r.users }} · {{ r.pct }}%</span>
                  </div>
                  <div style="height: 5px; background: var(--rule); border-radius: 3px; overflow: hidden">
                    <div :style="{ width: r.pct + '%', height: '100%', background: ROLE_COLOR[r.id] }"></div>
                  </div>
                </div>
              </div>
            </div>

            <div class="section-card">
              <div class="section-head"><div class="section-title"><Icon name="bolt" /> 最近活动</div></div>
              <div class="section-body flush">
                <div v-if="!audit.length" class="empty-state">暂无活动记录。</div>
                <div v-for="a in audit.slice(0, 4)" :key="a.id" class="activity-item">
                  <div class="activity-dot"><Icon :name="a.actor_name ? 'user' : 'sparkle'" /></div>
                  <div class="flex-1-min">
                    <div class="activity-text"><b>{{ a.actor_name || "系统" }}</b> · {{ a.action }} <em>{{ a.target }}</em></div>
                    <div class="activity-time">{{ fmtDate(a.ts) }} · {{ a.ip || "—" }}</div>
                  </div>
                  <span class="au-result" :class="a.result" style="height: fit-content">{{ a.result }}</span>
                </div>
              </div>
            </div>
          </div>
        </div>
      </template>

      <!-- ───────────── USERS ───────────── -->
      <template v-else-if="tab === 'users'">
        <div class="flex-between" style="align-items:flex-end;margin-bottom:14px">
          <div>
            <div class="heading-serif">用户管理</div>
            <div class="text-mute-sm" style="margin-top:2px">所有租户成员；本地账号、LDAP 同步和企业微信 SSO 统一在此管理。</div>
          </div>
          <button class="btn primary" @click="showCreate = !showCreate"><Icon name="plus" /> 新建用户</button>
        </div>

        <div v-if="showCreate" class="section-card" style="margin-bottom: 14px">
          <div style="padding: 14px 16px; display: flex; gap: 8px; flex-wrap: wrap; align-items: center">
            <input class="cfg-input" style="max-width: 140px" v-model="newUser.name" placeholder="姓名" />
            <input class="cfg-input" style="max-width: 190px" v-model="newUser.email" placeholder="邮箱" />
            <input class="cfg-input" style="max-width: 160px" type="password" v-model="newUser.password" placeholder="初始密码(≥8)" />
            <input class="cfg-input" style="max-width: 120px" v-model="newUser.department" placeholder="部门" />
            <select class="cfg-input" style="max-width: 130px" v-model="newUser.role"><option v-for="r in ROLES" :key="r" :value="r">{{ ROLE_NAME[r] }}</option></select>
            <button class="btn primary" :disabled="creating" @click="createUser">创建</button>
            <span v-if="createError" style="color: var(--danger); font-size: 12.5px">{{ createError }}</span>
          </div>
        </div>

        <div class="users-toolbar">
          <div class="filter-input"><Icon name="search" /><input v-model="userQ" placeholder="按姓名 / 邮箱搜索" @keyup.enter="loadUsers" /></div>
          <button class="filter-select" :class="{ on: sourceFilter !== 'all' }" @click="sourceFilter = nextOf(['all', 'local', 'ldap', 'wecom'], sourceFilter)">来源：{{ sourceFilter === "all" ? "全部" : SOURCE_LABEL[sourceFilter] }} <Icon name="chevron_down" /></button>
          <button class="filter-select" :class="{ on: statusFilter !== 'all' }" @click="statusFilter = nextOf(['all', 'active', 'pending', 'inactive'], statusFilter)">状态：{{ statusFilter === "all" ? "全部" : STATUS_LABEL[statusFilter] }} <Icon name="chevron_down" /></button>
          <span class="flex-1"></span>
          <span class="text-mute-sm">共 {{ filteredUsers().length }} 位 · {{ users.length }} 总数</span>
        </div>

        <div class="users-table">
          <div class="ut-row head"><div>用户</div><div class="col-email">邮箱</div><div>角色</div><div class="col-dept">部门</div><div>来源</div><div>在线</div><div>状态</div><div></div></div>
          <div v-for="u in filteredUsers()" :key="u.id" class="ut-row">
            <div class="ut-user">
              <div class="mem-avatar" :style="{ background: u.color || '#b8852a' }">{{ u.initials || (u.name || "?").slice(0, 1) }}<span class="status" :class="u.status === 'active' ? 'online' : u.status === 'inactive' ? 'offline' : 'idle'"></span></div>
              <div style="min-width: 0"><div class="nm">{{ u.name }} <span style="color: var(--ink-mute); font-weight: 400; font-size: 11px; margin-left: 4px">@{{ u.handle || u.email.split("@")[0] }}</span></div><div class="em">{{ u.title || "—" }}</div></div>
            </div>
            <div class="col-email" style="font-size: 11.5px; color: var(--ink-soft); font-family: var(--font-mono)">{{ u.email }}</div>
            <div>
              <select class="role-pill" :class="u.role" :value="u.role" @change="changeRole(u, ($event.target as HTMLSelectElement).value)" style="border: none; cursor: pointer; appearance: none">
                <option v-for="r in ROLES" :key="r" :value="r">{{ ROLE_NAME[r] }}</option>
              </select>
            </div>
            <div class="col-dept" style="font-size: 12px; color: var(--ink-soft)">{{ u.department || "—" }}</div>
            <div><span class="source-pill" :class="u.source"><span class="dot"></span>{{ SOURCE_LABEL[u.source] || u.source }}</span></div>
            <div><span class="presence-cell" :class="getStatus(u.id)"><span class="dot"></span>{{ getStatus(u.id) === 'online' ? '在线' : '离线' }}</span></div>
            <div><button class="status-cell" :class="u.status" @click="toggleStatus(u)" style="background: none; border: none; cursor: pointer"><span class="dot"></span>{{ STATUS_LABEL[u.status] || u.status }}</button></div>
            <div style="display: flex; gap: 2px; justify-content: flex-end">
              <button class="icon-btn" title="切换状态" @click="toggleStatus(u)"><Icon name="logout" /></button>
            </div>
          </div>
        </div>
      </template>

      <!-- ───────────── ROLES (RBAC) ───────────── -->
      <template v-else-if="tab === 'roles'">
        <div class="flex-between" style="align-items:flex-end;margin-bottom:14px">
          <div>
            <div class="heading-serif">权限管理</div>
            <div class="text-mute-sm" style="margin-top:2px">基于角色（RBAC）。系统角色不可删除。团队内容权限在各团队「设置 · 内容权限」中配置。</div>
          </div>
        </div>

        <div class="proj-grid" style="margin-bottom: 24px; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr))">
          <div v-for="r in roles" :key="r.id" style="background: var(--bg-panel); border: 1px solid var(--rule); border-radius: 14px; padding: 14px">
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 6px">
              <span class="role-pill" :class="r.id" style="font-size: 11.5px; padding: 3px 10px">{{ r.name }}</span>
              <span v-if="r.system" class="perm-system-tag">系统</span>
            </div>
            <div style="font-size: 11.5px; color: var(--ink-mute); line-height: 1.5; min-height: 34px">{{ r.desc }}</div>
            <div style="margin-top: 10px; display: flex; align-items: center; justify-content: space-between">
              <span style="font-family: var(--font-serif); font-size: 18px; color: var(--ink); font-weight: 600">{{ r.users }}</span>
              <span style="font-size: 10.5px; color: var(--ink-mute)">位用户</span>
            </div>
          </div>
        </div>

        <div style="display:flex;align-items:center;gap:10px;margin-bottom:10px">
          <div style="font-family: var(--font-serif); font-size: 16px; font-weight: 600; color: var(--ink)">权限矩阵</div>
          <span v-if="isSuperAdmin" style="font-size:11.5px;color:var(--ink-mute);padding:2px 8px;background:rgba(184,133,42,0.1);border-radius:6px">点击单元格可切换权限</span>
          <span v-else style="font-size:11.5px;color:var(--ink-mute)">仅超级管理员可修改</span>
        </div>
        <div class="perm-table">
          <div class="perm-grid" :style="{ gridTemplateColumns: '1.6fr repeat(' + roles.length + ', 1fr)' }">
            <div class="cell head">权限</div>
            <div v-for="r in roles" :key="r.id" class="cell head center">{{ r.name }}</div>

            <template v-for="g in permissions" :key="g.group">
              <div class="group-row" :style="{ gridColumn: '1 / span ' + (roles.length + 1) }">{{ g.group }}</div>
              <template v-for="p in g.items" :key="p.id">
                <div class="cell">
                  {{ p.name }}
                  <span class="perm-system-tag" style="font-family: var(--font-mono)">{{ p.id }}</span>
                </div>
                <div v-for="r in roles" :key="r.id" class="cell center"
                  :style="isSuperAdmin ? 'cursor:pointer;' : ''"
                  :title="isSuperAdmin ? (p.roles.includes(r.id) ? '点击移除权限' : '点击授予权限') : ''"
                  @click="isSuperAdmin && togglePermission(p.id, r.id, p.roles.includes(r.id))"
                >
                  <div v-if="permTogglingKey === p.id + ':' + r.id" style="width:14px;height:14px;border:2px solid var(--accent);border-top-color:transparent;border-radius:50%;animation:spin 0.6s linear infinite;margin:auto"></div>
                  <div v-else-if="p.roles.includes(r.id)" class="perm-check" :style="isSuperAdmin ? 'transition:opacity 0.15s;' : ''"><Icon name="check" /></div>
                  <div v-else class="perm-blank" :style="isSuperAdmin ? 'min-width:14px;min-height:14px;border-radius:3px;border:1.5px dashed var(--rule);' : ''"></div>
                </div>
              </template>
            </template>
          </div>
        </div>
      </template>

      <!-- ───────────── IDENTITY ───────────── -->
      <template v-else-if="tab === 'identity'">
        <div class="flex-between" style="align-items:flex-end;margin-bottom:14px">
          <div>
            <div class="heading-serif">身份与连接器</div>
            <div class="text-mute-sm" style="margin-top:2px">集中管理 LDAP / AD 域、企业微信 SSO 及其他身份源。用户身份信息会按映射规则同步进 Hermes。</div>
          </div>
        </div>

        <div class="ip-grid">
          <button v-for="p in providers" :key="p.id" class="ip-card" :class="{ active: activeProvider === p.id, disabled: !p.enabled }" @click="activeProvider = p.id">
            <div class="ip-card-head">
              <div class="ip-logo" :style="{ background: providerColor(p.id) }">{{ providerLetter(p.id) }}</div>
              <div class="flex-1-min">
                <div class="ip-name">{{ p.label }}</div>
                <div class="ip-meta">{{ p.enabled ? "已配置 · 直绑认证" : "尚未启用" }}</div>
              </div>
              <span class="ip-status" :class="{ off: !p.enabled }" @click.stop="toggleProvider(p)" style="cursor: pointer"><span class="dot"></span>{{ p.enabled ? "已启用" : "点击启用" }}</span>
            </div>
            <div v-if="!p.enabled" style="margin-top: 8px; font-size: 11.5px; color: var(--ink-mute)">点击启用并配置 {{ p.label }}</div>
          </button>
        </div>

        <!-- Detail panel -->
        <div class="ip-detail" v-if="activeProviderObj">
          <div class="ip-detail-head">
            <div class="ip-logo" :style="{ background: providerColor(activeProvider), width: '44px', height: '44px', borderRadius: '11px' }">{{ providerLetter(activeProvider) }}</div>
            <div class="flex-1">
              <div style="font-family: var(--font-serif); font-size: 20px; font-weight: 600; color: var(--ink)">{{ activeProviderObj.label }}</div>
              <div style="font-size: 11.5px; color: var(--ink-mute); margin-top: 2px">{{ providerSubtitle(activeProvider) }}</div>
            </div>
            <div style="display: flex; gap: 6px; align-items: center">
              <button class="btn" @click="toggleProvider(activeProviderObj)"><Icon name="bolt" /> {{ activeProviderObj.enabled ? "停用" : "启用" }}</button>
              <button v-if="activeProvider === 'ldap'" class="btn primary" :disabled="ldapSaving" @click="saveLdap"><Icon name="check" /> {{ ldapSaving ? "保存中…" : "保存" }}</button>
              <button v-if="activeProvider === 'wecom'" class="btn primary" :disabled="wecomSaving" @click="saveWecom"><Icon name="check" /> {{ wecomSaving ? "保存中…" : "保存" }}</button>
            </div>
          </div>

          <div class="ip-detail-body">

            <!-- ── LDAP / AD ──────────────────────────────────────── -->
            <template v-if="activeProvider === 'ldap' && ldap">

              <!-- 连接配置 -->
              <div class="cfg-section">
                <div class="cfg-section-title"><Icon name="globe" /> 连接配置</div>
                <div class="cfg-grid">
                  <div class="lbl">服务器地址</div>
                  <div class="val"><input class="cfg-input" v-model="ldap.config['host']" placeholder="ldap.company.com 或 192.168.1.100" /></div>
                  <div class="lbl">端口</div>
                  <div class="val" style="display:flex;gap:8px;align-items:center">
                    <input class="cfg-input short" v-model="ldap.config['port']" type="number" min="1" max="65535" placeholder="389" />
                    <label style="display:flex;align-items:center;gap:5px;font-size:12.5px;cursor:pointer;user-select:none">
                      <input type="checkbox" v-model="ldap.config['use_ssl']" />
                      使用 LDAPS (636)
                    </label>
                    <label style="display:flex;align-items:center;gap:5px;font-size:12.5px;cursor:pointer;user-select:none">
                      <input type="checkbox" v-model="ldap.config['start_tls']" />
                      StartTLS
                    </label>
                  </div>
                </div>
              </div>

              <!-- 认证方式 -->
              <div class="cfg-section">
                <div class="cfg-section-title"><Icon name="user" /> 认证方式</div>
                <div style="display:flex;gap:16px;margin-bottom:14px">
                  <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
                    <input type="radio" v-model="ldap.config['auth_mode']" value="direct" />
                    <div>
                      <div style="font-weight:600">直连绑定</div>
                      <div style="font-size:11.5px;color:var(--ink-mute)">用户 DN 由模板生成，直接绑定验证</div>
                    </div>
                  </label>
                  <label style="display:flex;align-items:center;gap:6px;cursor:pointer;font-size:13px">
                    <input type="radio" v-model="ldap.config['auth_mode']" value="search" />
                    <div>
                      <div style="font-weight:600">搜索绑定（推荐）</div>
                      <div style="font-size:11.5px;color:var(--ink-mute)">服务账号搜索用户 DN，再验证用户密码</div>
                    </div>
                  </label>
                </div>

                <!-- direct-bind fields -->
                <div v-if="ldap.config['auth_mode'] !== 'search'" class="cfg-grid">
                  <div class="lbl">用户 DN 模板</div>
                  <div class="val">
                    <input class="cfg-input" v-model="ldap.config['user_dn_template']" placeholder="uid={username},ou=people,dc=company,dc=com" />
                    <div class="text-mute-xs" style="margin-top:4px"><code class="mono">{username}</code> 将被替换为登录用户名</div>
                  </div>
                </div>

                <!-- search-bind fields -->
                <div v-else class="cfg-grid">
                  <div class="lbl">服务账号 DN</div>
                  <div class="val"><input class="cfg-input" v-model="ldap.config['bind_dn']" placeholder="cn=svc-hermes,ou=service,dc=company,dc=com" /></div>
                  <div class="lbl">服务账号密码</div>
                  <div class="val" style="display:flex;gap:6px">
                    <input class="cfg-input" style="flex:1" :type="ldapShowPw ? 'text' : 'password'" v-model="ldap.config['bind_password']" placeholder="服务账号密码" autocomplete="new-password" />
                    <button class="btn" style="flex-shrink:0" @click="ldapShowPw = !ldapShowPw">{{ ldapShowPw ? "隐藏" : "显示" }}</button>
                  </div>
                  <div class="lbl">搜索基 DN</div>
                  <div class="val"><input class="cfg-input" v-model="ldap.config['base_dn']" placeholder="ou=people,dc=company,dc=com" /></div>
                  <div class="lbl">用户搜索过滤器</div>
                  <div class="val">
                    <input class="cfg-input" v-model="ldap.config['search_filter']" placeholder="(uid={username})" />
                    <div class="text-mute-xs" style="margin-top:4px">常见格式：<code class="mono">(uid={username})</code> 或 <code class="mono">(sAMAccountName={username})</code>（AD）</div>
                  </div>
                </div>
              </div>

              <!-- 属性映射 -->
              <div class="cfg-section">
                <div class="cfg-section-title"><Icon name="folder" /> 属性映射</div>
                <div class="cfg-grid">
                  <div class="lbl">邮箱属性</div>
                  <div class="val"><input class="cfg-input short" v-model="ldap.config['attr_email']" placeholder="mail" /></div>
                  <div class="lbl">姓名属性</div>
                  <div class="val"><input class="cfg-input short" v-model="ldap.config['attr_name']" placeholder="cn" /></div>
                  <div class="lbl">部门属性</div>
                  <div class="val"><input class="cfg-input short" v-model="ldap.config['attr_dept']" placeholder="departmentNumber" /></div>
                  <div class="lbl">邮箱域补全</div>
                  <div class="val">
                    <input class="cfg-input short" v-model="ldap.config['email_domain']" placeholder="company.com" />
                    <div class="text-mute-xs" style="margin-top:4px">当 LDAP 无邮箱属性时，用 <code class="mono">username@{域}</code> 生成</div>
                  </div>
                </div>
              </div>

              <!-- 连接测试 -->
              <div class="cfg-section">
                <div class="cfg-section-title"><Icon name="bolt" /> 连接测试</div>
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                  <button class="btn primary" :disabled="ldapTesting" @click="testLdap">
                    <Icon name="refresh" :size="13" /> {{ ldapTesting ? "测试中…" : "测试连接" }}
                  </button>
                  <span v-if="ldapTestResult" :style="{ color: ldapTestResult.ok ? 'var(--ok)' : 'var(--danger)', fontSize: '13px' }">
                    {{ ldapTestResult.ok ? "✓" : "✗" }} {{ ldapTestResult.message }}
                  </span>
                </div>
                <div class="text-mute-xs" style="margin-top:8px">
                  先保存配置再测试；测试仅验证服务器连通性和服务账号绑定，不验证普通用户凭证。
                </div>
              </div>

              <!-- 部门 → 团队映射 -->
              <div class="cfg-section">
                <div class="cfg-section-title"><Icon name="user" /> 部门 → 团队映射</div>
                <div class="map-table">
                  <div class="map-row head"><div>department 属性值</div><div></div><div>默认角色</div><div>自动加入团队</div><div></div><div></div></div>
                  <div v-for="m in mappings" :key="m.id" class="map-row">
                    <div style="font-family:var(--font-mono);font-size:11.5px">{{ m.source_value }}</div>
                    <div class="map-arrow">→</div>
                    <div><span class="map-pill role">{{ ROLE_NAME[m.default_role] || m.default_role }}</span></div>
                    <div><span class="map-pill team" :class="{ none: !m.auto_join_team_id }"><Icon v-if="m.auto_join_team_id" name="user" :size="11" /> {{ teamName(m.auto_join_team_id) }}</span></div>
                    <div></div>
                    <div><button class="map-del" @click="deleteMapping(m.id)" title="删除此映射"><Icon name="close" :size="13" /></button></div>
                  </div>
                  <div class="map-row">
                    <div><input class="cfg-input" style="font-size:11.5px" v-model="newMap.source_value" placeholder="例如：研发部" /></div>
                    <div class="map-arrow">→</div>
                    <div><select class="cfg-input" v-model="newMap.default_role"><option value="member">成员</option><option value="team_admin">团队管理员</option><option value="viewer">只读</option></select></div>
                    <div><select class="cfg-input" v-model="newMap.auto_join_team_id"><option value="">不自动加入</option><option v-for="t in teamsOpt" :key="t.id" :value="t.id">{{ t.name }}</option></select></div>
                    <div></div>
                    <div><button class="map-del" style="color:var(--accent-deep)" @click="addMapping" title="添加映射"><Icon name="plus" :size="14" /></button></div>
                  </div>
                </div>
                <div style="margin-top:10px;font-size:11px;color:var(--ink-mute);line-height:1.5">
                  用户登录时按上表匹配部门属性；命中后赋予对应角色，并自动加入指定团队。
                </div>
              </div>
            </template>

            <!-- ── 企业微信 SSO ────────────────────────────────────── -->
            <template v-else-if="activeProvider === 'wecom' && wecom">

              <!-- 接入说明 -->
              <div class="cfg-section" style="background:var(--bg-plate);border-radius:var(--r-md);padding:14px 18px;margin-bottom:4px">
                <div style="font-size:12.5px;color:var(--ink-mute);line-height:1.7">
                  <b style="color:var(--ink)">接入步骤：</b>① 在 <a href="https://work.weixin.qq.com/wework_admin/frame#apps" target="_blank" style="color:var(--accent-deep)">企业微信后台</a> 创建自建应用，获取企业ID、AgentID 和 Secret；② 将回调地址域名配置到应用的「网页授权及JS-SDK」可信域名；③ 填写下方参数并保存；④ 点击「验证凭证」确认配置正确。<b>免登：</b>将应用「工作台主页」设为 <code class="mono">/api/v1/auth/wecom/silent?org=组织ID</code>，用户从工作台点击即免扫码登录。<b>多组织：</b>可添加多个企业微信组织，登录页将为每个组织提供独立的扫码入口。
                </div>
              </div>

              <!-- 每个组织一张卡片 -->
              <div v-for="org in wecomOrgs" :key="org.id" class="cfg-section wecom-org-card">
                <div class="wecom-org-head">
                  <Icon name="user" />
                  <input class="cfg-input wecom-org-name" v-model="org.name" placeholder="组织名称（如：北京公司）" />
                  <span class="wecom-org-id">{{ org.id }}</span>
                  <button class="map-del" style="margin-left:auto" title="删除此组织" @click="removeWecomOrg(org)"><Icon name="trash" :size="14" /></button>
                </div>

                <!-- 应用配置 -->
                <div class="cfg-grid" style="margin-top:8px">
                  <div class="lbl">企业 ID (CorpID)</div>
                  <div class="val"><input class="cfg-input" v-model="org.corp_id" placeholder="ww1234567890abcdef" /></div>
                  <div class="lbl">应用 AgentID</div>
                  <div class="val"><input class="cfg-input short" v-model="org.agent_id" placeholder="1000000" /></div>
                  <div class="lbl">应用密钥 (Secret)</div>
                  <div class="val" style="display:flex;gap:6px">
                    <input class="cfg-input" style="flex:1" :type="wecomSecretShown[org.id] ? 'text' : 'password'" v-model="org.app_secret" placeholder="应用 Secret（留空则不改动）" autocomplete="new-password" />
                    <button class="btn" style="flex-shrink:0" @click="wecomSecretShown[org.id] = !wecomSecretShown[org.id]">{{ wecomSecretShown[org.id] ? "隐藏" : "显示" }}</button>
                  </div>
                  <div class="lbl">扫码回调地址</div>
                  <div class="val">
                    <input class="cfg-input" v-model="org.redirect_uri" placeholder="https://hermes.company.com/api/v1/auth/wecom/callback" />
                    <div class="text-mute-xs" style="margin-top:4px">需与企业微信后台「可信域名」一致，路径为 <code class="mono">/api/v1/auth/wecom/callback</code></div>
                  </div>
                  <div class="lbl">免登回调地址</div>
                  <div class="val">
                    <input class="cfg-input" v-model="org.silent_redirect_uri" placeholder="同扫码回调（留空则复用）" />
                    <div class="text-mute-xs" style="margin-top:4px">工作台免登 OAuth2 回调，留空则复用扫码回调地址。</div>
                  </div>
                </div>

                <!-- 连接验证 -->
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin-top:10px">
                  <button class="btn primary" :disabled="wecomTestingOrg === org.id" @click="testWecomOrg(org)">
                    <Icon name="refresh" :size="13" /> {{ wecomTestingOrg === org.id ? "验证中…" : "保存并验证凭证" }}
                  </button>
                  <span v-if="wecomTestByOrg[org.id]" :style="{ color: wecomTestByOrg[org.id]!.ok ? 'var(--ok)' : 'var(--danger)', fontSize: '13px' }">
                    {{ wecomTestByOrg[org.id]!.ok ? "✓" : "✗" }} {{ wecomTestByOrg[org.id]!.message }}
                  </span>
                </div>

                <!-- 部门 → 团队映射（按组织） -->
                <div class="cfg-section-title" style="margin-top:16px"><Icon name="user" /> 部门 → 团队映射</div>
                <div class="map-table">
                  <div class="map-row head"><div>部门名称</div><div></div><div>默认角色</div><div>自动加入团队</div><div></div><div></div></div>
                  <div v-for="m in (wecomMappingsByOrg[org.id] || [])" :key="m.id" class="map-row">
                    <div style="font-family:var(--monofont, var(--font-mono));font-size:11.5px">{{ m.source_value }}</div>
                    <div class="map-arrow">→</div>
                    <div><span class="map-pill role">{{ ROLE_NAME[m.default_role] || m.default_role }}</span></div>
                    <div><span class="map-pill team" :class="{ none: !m.auto_join_team_id }"><Icon v-if="m.auto_join_team_id" name="user" :size="11" /> {{ teamName(m.auto_join_team_id) }}</span></div>
                    <div></div>
                    <div><button class="map-del" @click="deleteWecomMapping(org.id, m.id)" title="删除此映射"><Icon name="close" :size="13" /></button></div>
                  </div>
                  <div class="map-row">
                    <div><input class="cfg-input" style="font-size:11.5px" v-model="wecomMapDraft(org.id).source_value" placeholder="例如：研发部" /></div>
                    <div class="map-arrow">→</div>
                    <div><select class="cfg-input" v-model="wecomMapDraft(org.id).default_role"><option value="member">成员</option><option value="team_admin">团队管理员</option><option value="viewer">只读</option></select></div>
                    <div><select class="cfg-input" v-model="wecomMapDraft(org.id).auto_join_team_id"><option value="">不自动加入</option><option v-for="t in teamsOpt" :key="t.id" :value="t.id">{{ t.name }}</option></select></div>
                    <div></div>
                    <div><button class="map-del" style="color:var(--accent-deep)" @click="addWecomMapping(org)" title="添加映射"><Icon name="plus" :size="14" /></button></div>
                  </div>
                </div>
              </div>

              <button class="btn" style="margin-top:4px" @click="addWecomOrg"><Icon name="plus" :size="13" /> 添加组织</button>
              <div style="margin-top:10px;font-size:11px;color:var(--ink-mute);line-height:1.5">
                企业微信用户扫码登录后，Hermes 通过通讯录 API 拉取用户所在部门，按所属组织的映射表赋予角色、加入团队。点击右上角「保存」可统一保存所有组织配置。
              </div>
            </template>

            <!-- ── 其他连接器（SAML / OIDC / 飞书）──────────────── -->
            <template v-else>
              <div style="padding:40px;text-align:center;color:var(--ink-mute)">
                <Icon name="sparkle" />
                <div style="margin-top:12px;font-family:var(--font-serif);font-size:18px;font-weight:600;color:var(--ink)">{{ activeProviderObj.label }}</div>
                <div style="font-size:12.5px;margin-top:6px;max-width:420px;margin-left:auto;margin-right:auto;line-height:1.6">
                  {{ providerSubtitle(activeProvider) }}<br />
                  {{ activeProviderObj.enabled ? "已启用，可在此填写元数据 URL 与签名证书。" : "暂未启用，点击右上角「启用」后填写连接参数。" }}
                </div>
                <button class="btn primary" style="margin-top:14px" @click="toggleProvider(activeProviderObj)">{{ activeProviderObj.enabled ? "停用此连接器" : "启用此连接器" }}</button>
              </div>
            </template>

          </div>
        </div>
      </template>

      <!-- ───────────── AUDIT ───────────── -->
      <template v-else-if="tab === 'audit'">
        <div class="flex-between" style="align-items:flex-end;margin-bottom:14px">
          <div>
            <div class="heading-serif">审计日志</div>
            <div class="text-mute-sm" style="margin-top:2px">所有后台操作与登录事件都会被记录，按时间倒序。</div>
          </div>
          <div style="display: flex; align-items: center; gap: 8px">
            <button class="btn" :class="{ primary: auditAutoRefresh }" title="每 15 秒自动刷新" @click="toggleAutoRefresh">
              <Icon name="refresh" /> {{ auditAutoRefresh ? "自动刷新中" : "自动刷新" }}
            </button>
            <button class="btn" @click="loadAudit"><Icon name="refresh" /> 立即刷新</button>
          </div>
        </div>

        <!-- filters toolbar -->
        <div class="users-toolbar">
          <div class="filter-input"><Icon name="search" /><input v-model="auditQ" placeholder="按操作者、动作、目标或 IP 搜索" @input="loadAudit" /></div>
          <button class="filter-select" :class="{ on: resultFilter !== 'all' }"
            @click="resultFilter = nextOf(['all', 'ok', 'fail', 'partial'], resultFilter); loadAudit()">
            结果：{{ { all: "全部", ok: "成功", fail: "失败", partial: "部分成功" }[resultFilter] }} <Icon name="chevron_down" />
          </button>
          <span class="text-mute-sm">从</span>
          <input type="date" class="filter-select" v-model="auditDateFrom" style="padding: 0 10px; height: 34px" @change="loadAudit" />
          <span class="text-mute-sm">至</span>
          <input type="date" class="filter-select" v-model="auditDateTo" style="padding: 0 10px; height: 34px" @change="loadAudit" />
          <span class="flex-1"></span>
          <span class="text-mute-sm">{{ filteredAudit.length }} / {{ audit.length }} 条</span>
        </div>

        <div class="audit-table">
          <div class="au-row head"><div>时间戳</div><div>操作者</div><div>动作</div><div class="col-target">目标</div><div>结果</div><div class="col-ip">IP</div></div>
          <div v-if="!filteredAudit.length" class="empty-state-lg" style="padding:32px">暂无符合条件的记录。</div>
          <div v-for="a in filteredAudit" :key="a.id" class="au-row">
            <div class="au-ts">{{ fmtDate(a.ts) }}</div>
            <div class="au-actor">{{ a.actor_name || "系统" }}</div>
            <div style="font-family: var(--font-mono); font-size: 11.5px">{{ a.action }}</div>
            <div class="col-target">
              <div class="au-target">{{ a.target || "—" }}</div>
              <div v-if="a.meta && Object.keys(a.meta).length" class="au-meta">
                <span v-for="(v, k) in a.meta" :key="k">{{ k }}: {{ v }}</span>
              </div>
            </div>
            <div><span class="au-result" :class="a.result">{{ { ok: "成功", fail: "失败", partial: "部分成功" }[a.result] || a.result }}</span></div>
            <div class="col-ip au-ip">{{ a.ip || "—" }}</div>
          </div>
        </div>

        <div v-if="audit.length >= auditLimit" style="margin-top: 16px; text-align: center">
          <button class="btn" @click="loadMore">加载更多 <Icon name="chevron_down" /></button>
        </div>

        <div style="margin-top: 12px; font-size: 11.5px; color: var(--ink-faint); text-align: right">
          已加载最近 {{ auditLimit }} 条 · 后端最多返回 500 条
        </div>
      </template>

      <!-- ───────────── SYSTEM ───────────── -->
      <!-- ───────────── ASSISTANTS ───────────── -->
      <template v-else-if="tab === 'assistants'">
        <div class="flex-between" style="margin-bottom:14px">
          <div>
            <div class="heading-serif">助手管理</div>
            <div class="text-mute-sm" style="margin-top:2px">配置助手别名、图标与默认模型，用户在新建会话时即可看到。</div>
          </div>
          <div style="display: flex; gap: 8px; align-items: center">
            <span v-if="hermesVersion" style="font-size: 11.5px; color: var(--ink-mute); font-family: var(--font-mono); padding: 3px 8px; background: var(--bg-plate); border-radius: 5px">{{ hermesVersion }}</span>
            <button class="btn" :disabled="scanLoading" @click="scanAgents">
              <Icon name="refresh" :size="13" /> {{ scanLoading ? "扫描中…" : "扫描 ACP Agent" }}
            </button>
            <button class="btn" :disabled="scanProfilesLoading" @click="scanProfilesFn">
              <Icon name="sparkle" :size="13" /> {{ scanProfilesLoading ? "扫描中…" : "扫描 Profiles" }}
            </button>
            <button class="btn" @click="exportAllProfiles"><Icon name="download" :size="13" /> 导出全部</button>
            <button class="btn" @click="() => importFileRef?.click()"><Icon name="upload" :size="13" /> 导入</button>
            <input ref="importFileRef" type="file" accept=".json" style="display:none" @change="handleImportFile" />
            <button class="btn primary" @click="openCreateProfile"><Icon name="plus" /> 新建助手</button>
          </div>
        </div>

        <!-- Scan result banner -->
        <div v-if="scanMsg || scanErrors.length" style="margin-bottom: 14px; border-radius: 10px; overflow: hidden; border: 1px solid var(--rule)">
          <div v-if="scanMsg" class="info-banner">
            <Icon name="check" :size="14" style="color: var(--ok); flex-shrink: 0" />
            <span style="font-size: 13px; color: var(--ink)">{{ scanMsg }}</span>
            <span v-if="scanHermesPath" style="margin-left: auto; font-size: 11px; color: var(--ink-mute); font-family: var(--font-mono)">{{ scanHermesPath }}</span>
          </div>
          <template v-if="scanErrors.length">
            <div v-for="(e, i) in scanErrors" :key="i" style="padding: 8px 14px; display: flex; gap: 8px; align-items: flex-start; border-top: 1px solid var(--rule-soft); background: var(--bg-canvas)">
              <Icon name="alert_circle" :size="13" style="color: var(--warn); flex-shrink: 0; margin-top: 1px" />
              <span style="font-size: 12px; color: var(--ink-soft); line-height: 1.5">{{ e }}</span>
            </div>
            <div style="padding: 8px 14px; background: var(--bg-plate); border-top: 1px solid var(--rule-soft); font-size: 11.5px; color: var(--ink-mute)">
              Docker 用户：在 <code class="code-pill">.env</code> 中设置
              <code class="code-pill">HERMES_BIN=/path/to/hermes</code> 和
              <code class="code-pill">HERMES_HOME=/mounted/.hermes</code>，
              并在 <code class="code-pill">compose.yaml</code> 中取消注释 volumes 挂载，然后重启服务。
            </div>
          </template>
        </div>

        <!-- Profiles scan result banner -->
        <div v-if="scanProfilesMsg || scanProfilesErrors.length" style="margin-bottom: 14px; border-radius: 10px; overflow: hidden; border: 1px solid var(--rule)">
          <div v-if="scanProfilesMsg" class="info-banner">
            <Icon name="sparkle" :size="14" style="color: var(--ok); flex-shrink: 0" />
            <span style="font-size: 13px; color: var(--ink)">{{ scanProfilesMsg }}</span>
          </div>
          <template v-if="scanProfilesErrors.length">
            <div v-for="(e, i) in scanProfilesErrors" :key="i" style="padding: 8px 14px; display: flex; gap: 8px; align-items: flex-start; border-top: 1px solid var(--rule-soft); background: var(--bg-canvas)">
              <Icon name="alert_circle" :size="13" style="color: var(--warn); flex-shrink: 0; margin-top: 1px" />
              <span style="font-size: 12px; color: var(--ink-soft); line-height: 1.5">{{ e }}</span>
            </div>
          </template>
        </div>

        <!-- Profile form -->
        <div v-if="showProfileForm" class="section-card" style="margin-bottom: 14px; padding: 18px">
          <div style="font-size: 14px; font-weight: 600; color: var(--ink); margin-bottom: 14px">
            {{ editingProfileId ? "编辑助手" : "新建助手" }}
          </div>
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px">
            <label class="text-mute-sm">
              显示名称 *
              <input v-model="profileForm.name" placeholder="例：写作助手" class="form-input" />
            </label>
            <label class="text-mute-sm">
              标识符 (handle){{ editingProfileId ? '' : ' *' }}
              <input v-model="profileForm.handle" placeholder="例：writing-assistant" :disabled="!!editingProfileId" class="form-input" />
            </label>
            <label class="text-mute-sm">
              简介
              <input v-model="profileForm.desc" placeholder="助手描述" class="form-input" />
            </label>
            <label class="text-mute-sm">
              默认模型
              <input v-model="profileForm.default_model" placeholder="hermes-4" class="form-input mono" />
            </label>
            <label class="text-mute-sm">
              图标名称
              <input v-model="profileForm.icon" placeholder="sparkle" class="form-input" />
            </label>
            <label class="text-mute-sm">
              颜色
              <div style="display:flex;gap:6px;margin-top:4px;align-items:center">
                <input type="color" v-model="profileForm.color" style="width:36px;height:34px;border:1px solid var(--rule);border-radius:6px;cursor:pointer;padding:2px" />
                <input v-model="profileForm.color" class="form-input mono" style="flex:1;margin-top:0" />
              </div>
            </label>
            <label class="text-mute-sm">
              范围
              <select v-model="profileForm.scope" class="form-input">
                <option value="personal">个人</option>
                <option value="global">全局</option>
                <option value="team">团队</option>
              </select>
            </label>
            <label v-if="profileForm.scope === 'team'" class="text-mute-sm">
              关联团队
              <select v-model="profileForm.team_id" class="form-input">
                <option :value="null">不关联</option>
                <option v-for="t in teamsOpt" :key="t.id" :value="t.id">{{ t.name }}</option>
              </select>
            </label>
          </div>
          <!-- system_prompt -->
          <label class="text-mute-sm" style="display:block;margin-top:12px">
            系统提示词 (system prompt)
            <textarea v-model="profileForm.system_prompt" rows="4" placeholder="留空则使用助手默认行为" class="form-input" style="resize:vertical"></textarea>
          </label>
          <!-- skills chips -->
          <label class="text-mute-sm" style="display:block;margin-top:12px">
            技能标签
            <div style="margin-top:6px;display:flex;flex-wrap:wrap;gap:6px">
              <span v-for="s in profileForm.skills" :key="s" style="display:inline-flex;align-items:center;gap:4px;padding:2px 8px;border-radius:999px;background:rgba(184,133,42,0.12);color:var(--accent-deep);font-size:12px">
                {{ s }}
                <button type="button" style="background:none;border:none;cursor:pointer;padding:0;color:inherit;line-height:1;font-size:12px" @click="removeSkill(s)">&times;</button>
              </span>
              <input v-model="skillInput" placeholder="输入技能 + Enter" @keydown.enter.prevent="addSkill" style="padding:2px 8px;border:1px solid var(--rule);border-radius:999px;font-size:12px;background:var(--bg-canvas);color:var(--ink);outline:none;min-width:120px" />
            </div>
          </label>
          <!-- knowledge binding (injected into system_prompt) -->
          <label class="text-mute-sm" style="display:block;margin-top:12px">
            绑定知识库（其正文将注入助手上下文供复用）
            <div v-if="!knowledgeOptions.length" style="margin-top:6px;font-size:12px;color:var(--ink-mute)">暂无可绑定的团队知识。</div>
            <div v-else style="margin-top:6px;max-height:160px;overflow-y:auto;border:1px solid var(--rule);border-radius:8px;padding:8px;display:flex;flex-direction:column;gap:4px">
              <label v-for="k in knowledgeOptions" :key="k.id" style="display:flex;align-items:center;gap:8px;font-size:12px;cursor:pointer;color:var(--ink)">
                <input type="checkbox" :checked="profileForm.knowledge_ids.includes(k.id)" @change="toggleKnowledge(k.id)" style="width:13px;height:13px;cursor:pointer" />
                {{ k.label }}
              </label>
            </div>
          </label>
          <!-- MCP server binding (threaded into the agent's ACP session) -->
          <label class="text-mute-sm" style="display:block;margin-top:12px">
            绑定 MCP 服务器（该助手的会话将携带这些工具）
            <div v-if="!mcpServers.length" style="margin-top:6px;font-size:12px;color:var(--ink-mute)">暂无已注册的 MCP 服务器，请先在"MCP 服务器"标签页添加。</div>
            <div v-else style="margin-top:6px;max-height:160px;overflow-y:auto;border:1px solid var(--rule);border-radius:8px;padding:8px;display:flex;flex-direction:column;gap:4px">
              <label v-for="s in mcpServers" :key="s.name" style="display:flex;align-items:center;gap:8px;font-size:12px;cursor:pointer;color:var(--ink)">
                <input type="checkbox" :checked="profileForm.mcp_server_names.includes(s.name)" @change="toggleMcpServer(s.name)" style="width:13px;height:13px;cursor:pointer" />
                {{ s.name }}
              </label>
            </div>
          </label>
          <!-- MoA: fan this profile's messages out to reference profiles + synthesize -->
          <label style="display:flex;align-items:center;gap:8px;margin-top:12px;font-size:12.5px;color:var(--ink-mute);cursor:pointer">
            <input type="checkbox" v-model="profileForm.is_moa" style="width:14px;height:14px;cursor:pointer" />
            MoA 混合模型（选中该助手时，向下方参考助手并行提问并综合作答）
          </label>
          <label v-if="profileForm.is_moa" class="text-mute-sm" style="display:block;margin-top:8px">
            参考助手（至少选择 2 个以获得有意义的综合结果）
            <div v-if="profiles.filter((p) => p.id !== editingProfileId).length === 0" style="margin-top:6px;font-size:12px;color:var(--ink-mute)">暂无其他可选助手。</div>
            <div v-else style="margin-top:6px;max-height:160px;overflow-y:auto;border:1px solid var(--rule);border-radius:8px;padding:8px;display:flex;flex-direction:column;gap:4px">
              <label v-for="p in profiles.filter((p) => p.id !== editingProfileId)" :key="p.id" style="display:flex;align-items:center;gap:8px;font-size:12px;cursor:pointer;color:var(--ink)">
                <input type="checkbox" :checked="profileForm.moa_target_profile_ids.includes(p.id)" @change="toggleMoaTarget(p.id)" style="width:13px;height:13px;cursor:pointer" />
                {{ p.name }}
              </label>
            </div>
          </label>
          <!-- featured toggle -->
          <label style="display:flex;align-items:center;gap:8px;margin-top:12px;font-size:12.5px;color:var(--ink-mute);cursor:pointer">
            <input type="checkbox" v-model="profileForm.featured" style="width:14px;height:14px;cursor:pointer" />
            在首页推荐展示（featured）
          </label>
          <div v-if="profileError" style="margin-top: 10px; font-size: 12.5px; color: var(--danger)">{{ profileError }}</div>
          <div style="margin-top: 14px; display: flex; gap: 8px">
            <button class="btn primary" :disabled="profileSaving" @click="saveProfile">{{ profileSaving ? "保存中…" : "保存" }}</button>
            <button class="btn" @click="showProfileForm = false">取消</button>
          </div>
        </div>

        <!-- Profile list -->
        <div class="section-card">
          <div v-if="!profiles.length" class="empty-state-lg">
            还没有助手。点击「扫描 ACP Agent」自动生成，或手动「新建助手」。
          </div>
          <div v-for="p in profiles" :key="p.id" style="display:flex;align-items:center;gap:12px;padding:12px 16px;border-bottom:1px solid var(--rule-soft)">
            <div style="width:36px;height:36px;border-radius:8px;display:grid;place-items:center;color:#fff;flex-shrink:0" :style="{ background: p.color || '#b8852a' }">
              <Icon :name="p.icon || 'sparkle'" />
            </div>
            <div style="flex:1;min-width:0">
              <div style="font-size:13.5px;font-weight:600;color:var(--ink);display:flex;align-items:center;gap:6px">
                {{ p.name }}
                <span style="font-size:11.5px;color:var(--ink-mute);font-weight:400">@{{ p.handle }}</span>
                <span v-if="p.featured" title="首页推荐" style="font-size:13px">&#11088;</span>
              </div>
              <div style="font-size:12px;color:var(--ink-mute);margin-top:2px">{{ p.desc || "—" }}</div>
              <div v-if="p.skills && p.skills.length" style="display:flex;flex-wrap:wrap;gap:4px;margin-top:4px">
                <span v-for="s in p.skills" :key="s" style="padding:1px 6px;border-radius:999px;background:rgba(184,133,42,0.10);color:var(--accent-deep);font-size:11px">{{ s }}</span>
              </div>
              <div v-if="p.path" style="font-size:10.5px;color:var(--ink-mute);margin-top:2px;font-family:var(--font-mono);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:360px" :title="p.path">{{ p.path }}</div>
            </div>
            <span style="font-size:11px;padding:2px 8px;border-radius:999px;background:rgba(29,26,20,0.06);color:var(--ink-mute)">{{ SCOPE_LABEL[p.scope] || p.scope }}</span>
            <span style="font-size:10.5px;color:var(--ink-mute);font-family:var(--font-mono)">{{ p.default_model }}</span>
            <button class="icon-btn" title="克隆" @click="cloneProfile(p)"><Icon name="copy" :size="13" /></button>
            <button class="icon-btn" title="导出" @click="exportProfile(p)"><Icon name="download" :size="13" /></button>
            <button class="icon-btn" title="编辑" @click="openEditProfile(p)"><Icon name="edit" :size="13" /></button>
            <button class="icon-btn" title="删除" style="color:var(--danger)" @click="deleteProfileItem(p)"><Icon name="close" :size="13" /></button>
          </div>
        </div>
      </template>

      <!-- ── MCP 服务器 ── -->
      <template v-else-if="tab === 'mcp'">
        <div style="margin-bottom: 14px; display: flex; align-items: center; justify-content: space-between">
          <div class="heading-serif">MCP 服务器</div>
          <button class="btn primary" style="font-size: 12px" @click="showMcpForm = !showMcpForm">+ 添加服务器</button>
        </div>

        <!-- Add form -->
        <div v-if="showMcpForm" class="section-card" style="margin-bottom: 16px">
          <div class="section-head"><div class="section-title">新增 MCP 服务器</div></div>
          <div style="padding: 16px; display: grid; grid-template-columns: 130px 1fr; gap: 12px 14px; align-items: center; font-size: 13px">
            <div class="text-mute">名称 *</div>
            <input class="cfg-input" v-model="mcpForm.name" placeholder="如 filesystem" />
            <div class="text-mute">传输方式</div>
            <select class="cfg-input" style="height: 34px" v-model="mcpForm.transport">
              <option value="stdio">stdio（本地命令）</option>
              <option value="http">http（远程 URL）</option>
            </select>
            <template v-if="mcpForm.transport === 'stdio'">
              <div class="text-mute">命令行</div>
              <input class="cfg-input" v-model="mcpForm.command" placeholder="如 npx -y @modelcontextprotocol/server-filesystem /tmp" />
            </template>
            <template v-else>
              <div class="text-mute">URL</div>
              <input class="cfg-input" v-model="mcpForm.url" placeholder="如 http://localhost:8888/mcp" />
            </template>
            <div class="text-mute">环境变量</div>
            <textarea class="cfg-input" style="min-height: 64px; padding-top: 8px; resize: vertical"
              v-model="mcpForm.envStr" placeholder="每行一个，格式：KEY=VALUE"></textarea>
          </div>
          <div v-if="mcpError" style="padding: 0 16px 12px; font-size: 12.5px; color: var(--danger)">{{ mcpError }}</div>
          <div style="padding: 0 16px 16px; display: flex; gap: 8px">
            <button class="btn primary" :disabled="mcpSaving || !mcpForm.name.trim()" @click="addMcpServer">
              {{ mcpSaving ? '添加中…' : '确认添加' }}
            </button>
            <button class="btn" @click="showMcpForm = false; mcpError = ''">取消</button>
          </div>
        </div>

        <!-- Server list -->
        <div v-if="mcpLoading" style="padding: 24px; text-align: center; color: var(--ink-mute); font-size: 13px">加载中…</div>
        <div v-else-if="!mcpServers.length" class="section-card">
          <div style="padding: 24px; text-align: center; color: var(--ink-mute); font-size: 13px">
            暂无 MCP 服务器配置。点击右上角「添加服务器」开始注册。
          </div>
        </div>
        <div v-else class="section-card">
          <div class="section-body flush">
            <div v-for="s in mcpServers" :key="s.name" class="row-item" style="padding: 12px 16px; gap: 12px">
              <div style="flex: 1; min-width: 0">
                <div style="font-size: 13.5px; font-weight: 600; color: var(--ink); display:flex; align-items:center; gap:6px">
                  {{ s.name }}
                  <span v-if="s.transport === 'http'" class="mcp-status-dot" :class="mcpStatuses[s.name]?.reachable ? 'ok' : 'unknown'"
                    :title="mcpStatuses[s.name] ? (mcpStatuses[s.name].reachable ? '已连接' : '未连接') : ''"></span>
                </div>
                <div style="font-size: 11.5px; color: var(--ink-mute); margin-top: 3px; display: flex; gap: 10px; flex-wrap: wrap">
                  <span class="role-pill" style="font-size: 10px; padding: 1px 6px">{{ s.transport }}</span>
                  <span v-if="s.command" style="font-family: monospace">{{ s.command }}</span>
                  <span v-else-if="s.url">{{ s.url }}</span>
                </div>
              </div>
              <button v-if="s.transport === 'http'" class="btn" style="font-size: 11px; flex-shrink: 0" @click="async () => { mcpStatuses[s.name] = await checkMcpStatus(s.name); }">检测</button>
              <button class="btn text-danger" style="font-size: 12px; flex-shrink: 0" @click="deleteMcpServer(s.name)">删除</button>
            </div>
          </div>
        </div>
      </template>

      <template v-else-if="tab === 'system' && settings">
        <div style="margin-bottom: 14px">
          <div class="heading-serif">系统设置</div>
          <div class="text-mute-sm" style="margin-top:2px">租户级配置：品牌、模型网关、容量配额。</div>
        </div>
        <div class="col-grid">
          <div class="section-card">
            <div class="section-head"><div class="section-title"><Icon name="sparkle" /> 品牌与外观</div></div>
            <div style="padding: 18px"><div class="cfg-grid">
              <div class="lbl">租户名称</div><div class="val"><input class="cfg-input" v-model="settings.branding.tenant_name" placeholder="组织 / 租户名" /></div>
              <div class="lbl">显示标识</div><div class="val"><input class="cfg-input" v-model="settings.branding.display" placeholder="浏览器标题、登录页主标题" /></div>
              <div class="lbl">短名</div><div class="val"><input class="cfg-input" v-model="settings.branding.short_name" placeholder="侧栏、通知前缀、兜底名" /></div>
              <div class="lbl">登录页文案</div><div class="val"><input class="cfg-input" v-model="settings.branding.login_tagline" placeholder="登录页 / 问候语 slogan" /></div>
              <div class="lbl">登录页副标题</div><div class="val"><input class="cfg-input" v-model="settings.branding.login_subtitle" placeholder="登录页主标题下方的说明行" /></div>
              <div class="lbl">主题强调色</div><div class="val"><div style="display:flex;align-items:center;gap:8px"><input type="color" v-model="settings.branding.accent" style="width:34px;height:28px;border:1px solid var(--rule);border-radius:6px;background:transparent;cursor:pointer;padding:2px" /><input class="cfg-input" style="flex:1;min-width:0" v-model="settings.branding.accent" placeholder="#b8852a" /></div></div>
              <div class="lbl">网站图标</div><div class="val">
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                  <img v-if="liveBranding.favicon_url" :src="liveBranding.favicon_url" alt="" style="width:24px;height:24px;border-radius:5px;object-fit:contain;border:1px solid var(--rule)" />
                  <span v-else style="font-size:11.5px;color:var(--ink-mute);font-style:italic">未上传</span>
                  <label class="btn" style="cursor:pointer;font-size:11.5px;padding:4px 10px">
                    {{ assetBusy === 'favicon' ? '上传中…' : (liveBranding.favicon_url ? '更换' : '上传') }}
                    <input type="file" accept="image/png,image/svg+xml,image/webp,image/x-icon,image/vnd.microsoft.icon" style="display:none" :disabled="assetBusy === 'favicon'" @change="onAssetPicked('favicon', $event)" />
                  </label>
                  <button v-if="liveBranding.favicon_url" class="btn" style="font-size:11.5px;padding:4px 10px;color:var(--danger);border-color:color-mix(in srgb,var(--danger) 40%,var(--rule))" :disabled="assetBusy === 'favicon'" @click="onAssetClear('favicon')">清除</button>
                </div>
              </div>
              <div class="lbl">品牌 Logo</div><div class="val">
                <div style="display:flex;align-items:center;gap:10px;flex-wrap:wrap">
                  <img v-if="liveBranding.logo_url" :src="liveBranding.logo_url" alt="" style="width:34px;height:34px;border-radius:6px;object-fit:contain;border:1px solid var(--rule);background:var(--bg-canvas)" />
                  <span v-else style="font-size:11.5px;color:var(--ink-mute);font-style:italic">未上传</span>
                  <label class="btn" style="cursor:pointer;font-size:11.5px;padding:4px 10px">
                    {{ assetBusy === 'logo' ? '上传中…' : (liveBranding.logo_url ? '更换' : '上传') }}
                    <input type="file" accept="image/png,image/svg+xml,image/webp,image/jpeg" style="display:none" :disabled="assetBusy === 'logo'" @change="onAssetPicked('logo', $event)" />
                  </label>
                  <button v-if="liveBranding.logo_url" class="btn" style="font-size:11.5px;padding:4px 10px;color:var(--danger);border-color:color-mix(in srgb,var(--danger) 40%,var(--rule))" :disabled="assetBusy === 'logo'" @click="onAssetClear('logo')">清除</button>
                </div>
              </div>
            </div>
            <div style="padding:0 18px 14px;font-size:11px;color:var(--ink-mute);font-style:italic;line-height:1.5">图标与 Logo 即时生效；名称 / 文案 / 强调色需点击下方「保存设置」。</div>
            </div>
          </div>
          <div class="section-card">
            <div class="section-head"><div class="section-title"><Icon name="bolt" /> 模型网关</div></div>
            <div style="padding: 18px"><div class="cfg-grid">
              <div class="lbl">默认模型</div><div class="val"><input class="cfg-input" v-model="settings.model_gateway.default_model" /></div>
              <div class="lbl">月度配额</div><div class="val"><input class="cfg-input short" type="number" v-model.number="settings.model_gateway.monthly_token_quota" /> <span style="font-size: 11px; color: var(--ink-mute)">tokens</span></div>
              <div class="lbl">速率限制</div><div class="val"><input class="cfg-input short" type="number" v-model.number="settings.model_gateway.rate_limit_per_min" /> <span style="font-size: 11px; color: var(--ink-mute)">/ 用户 / 分</span></div>
              <div class="lbl">超额行为</div><div class="val">
                <div style="display: inline-flex; background: rgba(29, 26, 20, 0.04); border-radius: 999px; padding: 2px">
                  <button v-for="o in [['soft', '软限制'], ['hard', '硬限制'], ['warn', '仅告警']]" :key="o[0]" :style="{ background: settings.model_gateway.overage === o[0] ? 'var(--bg-panel)' : 'transparent', color: settings.model_gateway.overage === o[0] ? 'var(--ink)' : 'var(--ink-mute)', padding: '4px 10px', borderRadius: '999px', fontSize: '11.5px', fontWeight: 500 }" @click="settings.model_gateway.overage = o[0]">{{ o[1] }}</button>
                </div>
              </div>
            </div></div>
          </div>
        </div>

        <div style="margin-top: 16px; display: flex; align-items: center; gap: 12px">
          <button class="btn primary" :disabled="savingSettings" @click="saveSettings">{{ savingSettings ? "保存中…" : "保存设置" }}</button>
          <span style="font-size: 12px; color: var(--ink-mute); font-style: italic">速率限制改动即时生效。</span>
        </div>

        <div class="section-card" style="margin-top: 18px; border-color: color-mix(in srgb, var(--danger) 30%, var(--rule))">
          <div class="section-head"><div class="section-title text-danger">危险区</div></div>
          <div style="padding: 14px 18px; display: flex; justify-content: space-between; align-items: center">
            <div>
              <div style="font-weight: 600; color: var(--ink)">迁移到独立部署</div>
              <div class="text-mute-sm" style="margin-top:2px">导出全部租户数据并切换到本地化部署，过程中服务会中断 ~15 分钟。</div>
            </div>
            <button class="btn" style="color: var(--danger); border-color: var(--danger)" disabled title="需在独立部署环境中执行">开始迁移</button>
          </div>
        </div>
      </template>

      <!-- Performance tab -->
      <template v-else-if="tab === 'performance'">
        <div style="padding: 20px">
          <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
            <h1 class="admin-title" style="margin:0"><em>性能</em>监控</h1>
            <button class="btn" @click="loadPerformance" :disabled="perfLoading">
              <Icon name="refresh" :size="13" /> {{ perfLoading ? "加载中…" : "刷新" }}
            </button>
          </div>

          <div v-if="perfLoading && !perfData" style="text-align:center;padding:40px;color:var(--ink-mute)">加载中…</div>
          <div v-else-if="!perfData" style="text-align:center;padding:40px;color:var(--ink-mute)">暂无数据</div>

          <div v-else style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:12px">
            <!-- Process -->
            <div class="section-card" style="padding:16px">
              <div class="section-head"><div class="section-title">进程</div></div>
              <div style="font-size:13px;line-height:1.8;color:var(--ink)">
                <div>CPU: <strong>{{ (perfData as any).process?.cpu_percent }}%</strong></div>
                <div>内存: <strong>{{ (perfData as any).process?.memory_mb }}MB</strong> ({{ (perfData as any).process?.memory_percent }}%)</div>
                <div>线程: <strong>{{ (perfData as any).process?.threads }}</strong></div>
                <div>运行: <strong>{{ Math.floor(((perfData as any).process?.uptime_seconds || 0) / 3600) }}h {{ Math.floor((((perfData as any).process?.uptime_seconds || 0) % 3600) / 60) }}m</strong></div>
              </div>
            </div>
            <!-- System -->
            <div class="section-card" style="padding:16px">
              <div class="section-head"><div class="section-title">系统</div></div>
              <div style="font-size:13px;line-height:1.8;color:var(--ink)">
                <div>CPU: <strong>{{ (perfData as any).system?.cpu_percent }}%</strong></div>
                <div>内存: <strong>{{ (perfData as any).system?.memory_used_mb }}MB / {{ (perfData as any).system?.memory_total_mb }}MB</strong> ({{ (perfData as any).system?.memory_percent }}%)</div>
                <div v-if="(perfData as any).system?.disk_percent != null">磁盘: <strong>{{ (perfData as any).system?.disk_percent }}%</strong></div>
              </div>
            </div>
            <!-- Database -->
            <div class="section-card" style="padding:16px">
              <div class="section-head"><div class="section-title">数据库</div></div>
              <div style="font-size:13px;line-height:1.8;color:var(--ink)">
                <div>会话: <strong>{{ (perfData as any).database?.conversations }}</strong></div>
                <div>消息: <strong>{{ (perfData as any).database?.messages }}</strong></div>
                <div>用户: <strong>{{ (perfData as any).database?.users }}</strong></div>
              </div>
            </div>
            <!-- Redis -->
            <div class="section-card" style="padding:16px">
              <div class="section-head"><div class="section-title">Redis</div></div>
              <div style="font-size:13px;line-height:1.8;color:var(--ink)">
                <div>状态: <strong :style="{ color: (perfData as any).redis?.connected ? 'var(--ok)' : 'var(--danger)' }">{{ (perfData as any).redis?.connected ? '已连接' : '未连接' }}</strong></div>
                <div>内存: <strong>{{ (perfData as any).redis?.used_memory_mb }}MB</strong></div>
              </div>
            </div>
          </div>
        </div>
      </template>
    </div>

    <!-- Import preview modal -->
    <Teleport to="body">
      <div v-if="importPreview.visible" class="ctx-menu-scrim" style="z-index: 9999" @click="importPreview.visible = false">
        <div class="import-preview" @click.stop>
          <div class="import-preview-title">导入预览 — 将创建 {{ importPreview.items.length }} 个助手</div>
          <div class="import-preview-list">
            <div v-for="(item, i) in importPreview.items" :key="i" class="import-preview-item">
              <Icon name="sparkle" :size="12" />
              <span>{{ item.name || '未命名' }}</span>
              <span class="import-preview-handle">@{{ item.handle || 'N/A' }}</span>
            </div>
          </div>
          <div class="import-preview-actions">
            <button class="btn" @click="importPreview.visible = false">取消</button>
            <button class="btn primary" @click="confirmImport">确认导入</button>
          </div>
        </div>
      </div>
    </Teleport>
  </div>
</template>
