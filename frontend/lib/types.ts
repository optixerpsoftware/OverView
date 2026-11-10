// Types alignés sur les serializers DRF du backend.

export type UUID = string;

export interface Organization {
  id: UUID;
  name: string;
  description: string | null;
  create_at: string;
  modified_at: string;
}

export interface Connector {
  id: UUID;
  org_id: UUID;
  name: string;
  tenant_id: UUID;
  client_id: UUID;
  is_active: boolean;
  last_checked_at: string | null;
  last_error: string;
  create_at: string;
  modified_at: string;
}

export interface ConnectorCreate {
  org_id: UUID;
  name: string;
  tenant_id: string;
  client_id: string;
  client_secret: string;
}

export interface ConnectorTestResult {
  ok: boolean;
  error: string;
}

export interface Mailbox {
  id: UUID;
  org_id: UUID;
  connector_id: UUID;
  user_principal_name: string;
  graph_user_id: string;
  is_active: boolean;
  last_synced_at: string | null;
  create_at: string;
  modified_at: string;
}

export interface OutlookRule {
  id: UUID;
  mailbox: UUID;
  rule_id: string;
  display_name: string;
  sequence: number;
  is_enabled: boolean;
  has_error: boolean;
  is_read_only: boolean;
  conditions: Record<string, unknown> | null;
  actions: Record<string, unknown> | null;
  create_at: string;
  modified_at: string;
}

export interface AvailableMailbox {
  user_principal_name: string;
  display_name: string;
  already_imported: boolean;
}

export interface ImportResult {
  imported: string[];
  already_present: number;
}

export interface SyncResult {
  rules: number;
}

export interface Note {
  text: string;
  error: boolean;
}

// ---- Sécurité d'un compte (module identity) ----
export interface Section<T> {
  items: T[];
  error: string;
}

export interface SecurityProfile {
  user_id: string;
  display_name: string;
  account_enabled: boolean | null;
  sessions_valid_from: string | null;
  last_password_change: string | null;
}

export interface Device {
  id: string;
  display_name: string;
  operating_system: string;
  operating_system_version: string;
  trust_type: string;
  is_compliant: boolean | null;
  is_managed: boolean | null;
  last_sign_in: string | null;
  registered_at: string | null;
  device_id: string;
}

export interface ActiveSession {
  app: string;
  client: string;
  ip: string;
  city: string;
  country: string;
  os: string;
  browser: string;
  first_seen: string | null;
  last_seen: string | null;
  count: number;
  interactive: boolean;
}

export interface AuthMethod {
  id: string;
  kind: string;
  label: string;
  detail: string;
  is_mfa: boolean;
}

export interface SignIn {
  at: string | null;
  app: string;
  ip: string;
  city: string;
  country: string;
  client: string;
  os: string;
  browser: string;
  interactive: boolean;
  success: boolean;
  error_code: number;
}

export interface SecurityAction {
  id: UUID;
  action: string;
  ok: boolean;
  detail: string;
  performed_by: string;
  create_at: string;
}

/** État de la synchronisation en arrière-plan d'un compte (un worker la rafraîchit toutes les 10 minutes). */
export interface SyncState {
  status: 'idle' | 'running' | 'failed';
  last_synced_at: string | null;
  last_error: string;
}

export interface SecurityOverview {
  mailbox: { id: UUID; user_principal_name: string };
  /** null tant que la toute première synchronisation n'est pas terminée. */
  profile: SecurityProfile | null;
  devices: Section<Device>;
  auth_methods: Section<AuthMethod>;
  actions: SecurityAction[];
  sync: SyncState;
}

// Connexions stockées localement (collectées de façon incrémentale) et sessions estimées.
export interface SecurityActivity {
  sign_ins: Section<SignIn>;
  active_sessions: Section<ActiveSession>;
  sync: SyncState;
}

export interface TapResult {
  pass: string;
  lifetime_minutes: number;
  once: boolean;
}

export interface TotpResult {
  secret: string;
  uri: string;
  account: string;
  serial: string;
}

export interface EmergencyStep {
  step: string;
  ok: boolean;
  detail: string;
}

export interface EmergencyResult {
  steps: EmergencyStep[];
  password: string | null;
  tap: { pass: string; lifetime_minutes: number; once: boolean } | null;
  ok: boolean;
}

// ---- Gouvernance du tenant (module compliance) ----
export type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info';
export type FindingStatus = 'pass' | 'fail' | 'unavailable' | 'error';
export type FindingCategory = 'mfa' | 'access' | 'policies' | 'posture';

export interface Finding {
  code: string;
  category: FindingCategory;
  title: string;
  severity: Severity;
  status: FindingStatus;
  summary: string;
  details: string[];
  recommendation: string;
  score: number;
  requires: string;
}

export interface Licenses {
  skus?: string[];
  entra_p1?: boolean;
  entra_p2?: boolean;
  error?: string;
}

export interface ComplianceSnapshot {
  id: UUID;
  connector_id: UUID;
  created_at: string;
  score: number | null;
  findings: Finding[];
  licenses: Licenses;
}

export interface ScorePoint {
  id: UUID;
  created_at: string;
  score: number | null;
}

// ---- Partage OneDrive / SharePoint (module onedrive) ----
export type Risk = 'critical' | 'high' | 'medium' | 'low' | 'info';

export interface Drive {
  id: string;
  label: string;
  owner: string;
  kind: 'onedrive' | 'sharepoint';
  web_url: string;
}

export interface DriveItem {
  id: string;
  name: string;
  is_folder: boolean;
  child_count: number | null;
  size: number;
  web_url: string;
  modified_at: string | null;
  path: string;
  shared: boolean;
}

export interface ShareEntry {
  permission_id: string;
  kind: 'link' | 'direct';
  scope: 'anonymous' | 'organization' | 'users' | 'direct';
  link_type: string;
  roles: string[];
  link_url: string;
  expires_at: string | null;
  has_password: boolean;
  grantees: string[];
  external: boolean;
  risk: Risk;
  inherited?: boolean;
}

export interface ShareRecord extends ShareEntry {
  drive_id: string;
  drive_label: string;
  item_id: string;
  name: string;
  path: string;
  web_url: string;
  is_folder: boolean;
}

export interface SharingSummary {
  total: number;
  anonymous: number;
  organization: number;
  external: number;
  anonymous_edit: number;
  no_expiry_anonymous: number;
  by_risk: Record<Risk, number>;
}

export interface SharingScan {
  progress?: { drives_done?: number; drives_total?: number; records?: number };
  id: UUID;
  connector_id: UUID;
  status: 'running' | 'done' | 'failed';
  created_at: string;
  finished_at: string | null;
  error: string;
  summary: SharingSummary | Record<string, never>;
  records: ShareRecord[];
  truncated: string[];
  errors: string[];
}

// ---- Réglages d'inscription des appareils (Entra ID) ----
export interface DeviceRegistrationSettings {
  user_device_quota: number | null;
  mfa_required: boolean;
  join_scope: 'all' | 'none' | 'selected';
  join_users: number;
  join_groups: number;
  join_configurable: boolean;
  register_scope: 'all' | 'none' | 'selected';
  register_users: number;
  register_groups: number;
  register_configurable: boolean;
  laps_enabled: boolean;
  join_user_ids?: string[];
  join_group_ids?: string[];
  join_groups_detail?: DirectoryGroup[];
}

export interface DirectoryGroup {
  id: string;
  name: string | null;
}

export interface SettingChange {
  id: UUID;
  changes: string[];
  ok: boolean;
  detail: string;
  performed_by: string;
  create_at: string;
}

// ---- Flux de messagerie et quarantaine (Exchange Online) ----
export type TraceState = 'delivered' | 'failed' | 'quarantined' | 'spam' | 'pending' | 'expanded' | 'other';

export interface TraceMessage {
  trace_id: string;
  message_id: string;
  received: string | null;
  sender: string;
  recipient: string;
  subject: string;
  status: string;
  size: number;
  from_ip: string;
  to_ip: string;
}

export interface TraceResult {
  summary: {
    total: number;
    by_state: Partial<Record<TraceState, number>>;
    per_hour: ({ hour: string } & Partial<Record<TraceState, number>>)[];
    top_senders: [string, number][];
    top_recipients: [string, number][];
    failure_rate: number;
  };
  messages: TraceMessage[];
  truncated: boolean;
}

export interface QuarantinedMessage {
  identity: string;
  message_id: string;
  received: string | null;
  expires: string | null;
  sender: string;
  recipients: string[];
  subject: string;
  type: string;
  release_status: string;
  released: boolean;
  size: number;
  direction: string;
  policy: string;
}

export interface QuarantineResult {
  summary: { total: number; by_type: Record<string, number>; pending_release: number; risky: number };
  messages: QuarantinedMessage[];
  truncated: boolean;
}

export interface QuarantineAction {
  id: UUID;
  identity: string;
  subject: string;
  quarantine_type: string;
  action: string;
  ok: boolean;
  detail: string;
  performed_by: string;
  create_at: string;
}

// ---- Vue transverse : tableau de bord, alertes, recherche, actions en lot ----
export type AlertSeverity = 'critical' | 'high' | 'medium' | 'low';
export type AlertCategory = 'sharing' | 'rule' | 'identity' | 'signin' | 'governance' | 'quarantine' | 'app' | 'protection';
export type AlertStatus = 'open' | 'acknowledged' | 'resolved';

export interface InsightAlert {
  id: UUID;
  connector_id: UUID;
  connector_name?: string;
  org_id: UUID | null;
  key: string;
  category: AlertCategory;
  severity: AlertSeverity;
  title: string;
  detail: string;
  link: string;
  data: Record<string, string>;
  status: AlertStatus;
  first_seen: string;
  last_seen: string;
  resolved_at: string | null;
  acknowledged_by: string;
  notified: boolean;
}

export type AlertCounts = Record<AlertSeverity, number> & { total: number };

export interface DashboardTenant {
  connector_id: UUID;
  name: string;
  org_id: UUID;
  org_name: string;
  is_active: boolean;
  connection_error: string;
  score: number | null;
  score_at: string | null;
  score_trend: number[];
  mailboxes: number;
  no_mfa: number;
  sharing: { at: string | null; external: number; anonymous: number; anonymous_edit: number };
  suspicious_rules: number;
  alerts: AlertCounts;
}

export interface Dashboard {
  totals: { tenants: number; mailboxes: number; no_mfa: number; external_shares: number; anonymous_links: number;
    anonymous_edit: number; suspicious_rules: number };
  alerts: AlertCounts;
  tenants: DashboardTenant[];
  top_alerts: InsightAlert[];
  charts: {
    trend: { day: string; critical: number; high: number; medium: number; low: number; resolved: number; active: number }[];
    by_severity: Record<AlertSeverity, number>;
    by_category: Partial<Record<AlertCategory, number>>;
    mttr_hours: number | null;
  };
}

export interface SearchHit {
  type: 'org' | 'tenant' | 'mailbox' | 'rule' | 'file';
  label: string;
  sub: string;
  href: string;
}

export interface NotificationSettings {
  emails: string[];
  min_severity: AlertSeverity;
  enabled: boolean;
  teams_configured: boolean;
}

export interface BulkResult {
  total: number;
  succeeded: number;
  failed: number;
  results: { id: string; ok: boolean; error?: string }[];
}
