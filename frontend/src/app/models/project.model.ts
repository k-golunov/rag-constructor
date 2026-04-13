/** Полное представление проекта (ProjectResponse из бэкенда). */
export interface Project {
  id: string;
  name: string;
  chunk_size: number;
  chunk_overlap: number;
  embedding_model: string;
  llm_model: string;
  system_prompt: string;
  created_at: string; // ISO-8601 datetime string
}

/** DTO для создания проекта (ProjectCreate из бэкенда). */
export interface ProjectCreate {
  name: string;
  chunk_size: number;
  chunk_overlap: number;
  embedding_model: string;
  llm_model: string;
  system_prompt: string;
}

/** Ответ бэкенда на запрос списка проектов (ProjectListResponse). */
export interface ProjectListResponse {
  total: number;
  items: Project[];
}

/** Статически заданные варианты моделей эмбеддингов. */
export const EMBEDDING_MODELS: { label: string; value: string }[] = [
  { label: 'OpenAI · text-embedding-3-small', value: 'text-embedding-3-small' },
  { label: 'OpenAI · text-embedding-3-large', value: 'text-embedding-3-large' },
  { label: 'OpenAI · text-embedding-ada-002',  value: 'text-embedding-ada-002' },
  {
    label: 'HuggingFace · all-MiniLM-L6-v2',
    value: 'sentence-transformers/all-MiniLM-L6-v2',
  },
  {
    label: 'HuggingFace · paraphrase-multilingual-MiniLM-L12-v2',
    value: 'sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2',
  },
];

/** Статически заданные варианты LLM-моделей. */
export const LLM_MODELS: { label: string; value: string }[] = [
  { label: 'OpenAI · GPT-4o',          value: 'gpt-4o' },
  { label: 'OpenAI · GPT-4o mini',      value: 'gpt-4o-mini' },
  { label: 'OpenAI · GPT-4 Turbo',      value: 'gpt-4-turbo' },
  { label: 'Anthropic · Claude 3.5 Sonnet', value: 'claude-3-5-sonnet-20241022' },
  { label: 'Anthropic · Claude 3 Haiku',    value: 'claude-3-haiku-20240307' },
];
