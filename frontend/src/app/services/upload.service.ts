import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface UploadSingleResponse {
  filename: string;
  project_id: string;
  chunk_size: number;
  chunk_overlap: number;
  chunks_count: number;
}

export interface UploadArchiveResponse {
  operation_id: string;
  message: string;
}

export interface OperationStatus {
  operation_id: string;
  status: string;
  result: unknown;
  error: string | null;
}

@Injectable({ providedIn: 'root' })
export class UploadService {
  private readonly baseUrl = `${environment.apiBaseUrl}/upload`;

  constructor(private readonly http: HttpClient) {}

  uploadFile(projectId: string, file: File): Observable<UploadSingleResponse> {
    const params = new HttpParams().set('project_id', projectId);
    const formData = new FormData();
    formData.append('file', file);
    return this.http.post<UploadSingleResponse>(`${this.baseUrl}/single`, formData, { params });
  }

  uploadArchive(projectId: string, file: File): Observable<UploadArchiveResponse> {
    const params = new HttpParams().set('project_id', projectId);
    const formData = new FormData();
    formData.append('file', file);
    return this.http.post<UploadArchiveResponse>(`${this.baseUrl}/archive`, formData, { params });
  }

  getOperationStatus(operationId: string): Observable<OperationStatus> {
    return this.http.get<OperationStatus>(`${this.baseUrl}/status/${operationId}`);
  }
}
