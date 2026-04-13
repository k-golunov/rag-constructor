/** Полное представление проекта (ProjectResponse из бэкенда). */
export interface Project {
  id: string;
  name: string;
  created_at: string;

  // Чанкинг
  chunk_size: number;
  chunk_overlap: number;
  split_by: string;
  chunking_strategy: string;
  extract_tables: boolean;

  // Эмбеддинги
  embedding_model: string;
  embedding_dimension: number;
  embedding_api_key: string | null;
  embedding_api_url: string | null;

  // LLM
  llm_model: string;
  llm_api_key: string | null;
  llm_api_url: string | null;

  // Промпт
  system_prompt: string;
}

/** DTO для создания проекта (ProjectCreate из бэкенда). */
export interface ProjectCreate {
  name: string;

  chunk_size: number;
  chunk_overlap: number;
  split_by: string;
  chunking_strategy: string;
  extract_tables: boolean;

  embedding_model: string;
  embedding_dimension: number;
  embedding_api_key?: string | null;
  embedding_api_url?: string | null;

  llm_model: string;
  llm_api_key?: string | null;
  llm_api_url?: string | null;

  system_prompt: string;
}

/**
 * DTO для частичного обновления проекта (ProjectUpdate из бэкенда).
 * Все поля опциональны — передаются только изменяемые.
 */
export interface ProjectUpdate {
  name?: string;

  chunk_size?: number;
  chunk_overlap?: number;
  split_by?: string;
  chunking_strategy?: string;
  extract_tables?: boolean;

  embedding_model?: string;
  embedding_dimension?: number;
  embedding_api_key?: string | null;
  embedding_api_url?: string | null;

  llm_model?: string;
  llm_api_key?: string | null;
  llm_api_url?: string | null;

  system_prompt?: string;
}

/** Ответ бэкенда на запрос списка проектов (ProjectListResponse). */
export interface ProjectListResponse {
  total: number;
  items: Project[];
}

// ─────────────────────────────────────────────────────────────
// Статические справочники
// ─────────────────────────────────────────────────────────────

export const EMBEDDING_MODELS: { label: string; value: string }[] = [
  { label: 'OpenAI · text-embedding-3-small', value: 'text-embedding-3-small' },
  { label: 'OpenAI · text-embedding-3-large', value: 'text-embedding-3-large' },
  { label: 'OpenAI · text-embedding-ada-002',  value: 'text-embedding-ada-002' },
  { label: 'HuggingFace · all-MiniLM-L6-v2',  value: 'sentence-transformers/all-MiniLM-L6-v2' },
  { label: 'HuggingFace · paraphrase-multilingual-MiniLM-L12-v2', value: 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2' },
];

export const LLM_MODELS: { label: string; value: string }[] = [
  { label: 'OpenAI · GPT-4o',              value: 'gpt-4o' },
  { label: 'OpenAI · GPT-4o mini',          value: 'gpt-4o-mini' },
  { label: 'OpenAI · GPT-4 Turbo',          value: 'gpt-4-turbo' },
  { label: 'Anthropic · Claude 3.5 Sonnet', value: 'claude-3-5-sonnet-20241022' },
  { label: 'Anthropic · Claude 3 Haiku',    value: 'claude-3-haiku-20240307' },
];

export const SPLIT_BY_OPTIONS: { label: string; value: string }[] = [
  { label: 'Абзацы',      value: 'paragraphs' },
  { label: 'Предложения', value: 'sentences'  },
  { label: 'Токены',      value: 'tokens'     },
];

export const CHUNKING_STRATEGIES: { label: string; value: string }[] = [
  { label: 'Recursive', value: 'recursive' },
];
