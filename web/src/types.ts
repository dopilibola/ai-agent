export interface Tenant {
  id: string;
  name: string;
  channels: string[];
  has_catalog: boolean;
  has_orders: boolean;
  has_services: boolean;
  has_catalog_import: boolean;
  has_doctors: boolean;
  has_prompts: boolean;
}

export interface PromptInfo {
  key: string;
  label: string;
  note: string;
  filename: string;
}

export interface PromptDetail extends PromptInfo {
  content: string;
}

export interface AnfaCatalogItem {
  id: number;
  tab: string;
  category: string;
  title: string;
  price: number; // UZS sum
  currency: string;
  active: boolean;
}

export interface AnfaDoctor {
  id: number;
  fullname: string;
  speciality: string;
  experience: string;
  schedule: Record<string, [number, number]>;
  hours_label: string;
  active: boolean;
}

export interface CatalogImportSummary {
  parsed: number;
  added: number;
  updated: number;
  removed: number;
  total: number;
}

export interface FlowerAmount {
  flower_name: string;
  quantity: number;
}

export interface Bouquet {
  id: string;
  branch_id: string;
  name: string;
  description: string;
  tags: string[];
  products_spent: FlowerAmount[];
  photo_url: string;
  price: number; // sum (UZS)
  active: boolean;
  created_at: string | null;
  updated_at: string | null;
}

export interface BouquetPage {
  items: Bouquet[];
  total: number;
  limit: number;
  offset: number;
}

export interface Order {
  id: number;
  chat_id: number;
  customer_name: string;
  customer_username: string | null;
  bouquet_name: string;
  bouquet_photo_url: string;
  bouquet_price_sum: number;
  delivery_fee_sum: number;
  total_sum: number;
  recipient_name: string;
  recipient_phone: string;
  address: string;
  delivery_time: string;
  card_text: string | null;
  is_surprise: boolean;
  extra_notes: string | null;
  status: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface RunTokens {
  input_tokens: number;
  cached_input_tokens: number;
  output_tokens: number;
  total_tokens: number;
}

export interface UsageRollup {
  tenant: string;
  name: string;
  chats: number;
  spent_total_tokens: number;
  spent_input_tokens: number;
  spent_cached_input_tokens: number;
  spent_output_tokens: number;
}

export interface UsageChat {
  chat_id: number;
  spent: RunTokens;
  current: RunTokens;
  updated_at: string | null;
}

export interface TenantUsage {
  tenant: string;
  totals: {
    spent_total_tokens: number;
    spent_input_tokens: number;
    spent_cached_input_tokens: number;
    spent_output_tokens: number;
  };
  chats: UsageChat[];
}

export interface Chat {
  chat_id: number;
  name: string | null;
  username: string | null;
  channels: string[];
  muted: boolean;
  spent_total_tokens: number;
  current_total_tokens: number;
  updated_at: string | null;
}

export interface ToolCall {
  name: string;
  args: unknown;
}

export interface Message {
  role: string;
  text: string;
  has_image: boolean;
  name?: string;
  tool_calls?: ToolCall[];
}

export interface Conversation {
  thread_id: string;
  tenant: string;
  channel: string;
  chat_id: number;
  messages: Message[];
}
