export interface Tenant {
  id: string;
  name: string;
  channels: string[];
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
