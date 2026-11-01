export type DataSourceStatus = 'pending' | 'processing' | 'completed' | 'failed';

export interface DataSource {
  id: string;
  project_id: string;
  file_name: string;
  file_path: string;
  status: DataSourceStatus;
  chunks_count: number;
  error_message: string | null;
  created_at: string;
  document_version_id: string | null;
}

export interface DataSourceListResponse {
  total: number;
  items: DataSource[];
}
