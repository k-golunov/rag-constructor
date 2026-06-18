import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';
import { DataSource, DataSourceListResponse } from '../models/data-source.model';

@Injectable({ providedIn: 'root' })
export class DataSourceService {
  private readonly baseUrl = `${environment.apiBaseUrl}/projects`;

  constructor(private readonly http: HttpClient) {}

  getDataSources(projectId: string): Observable<DataSourceListResponse> {
    return this.http.get<DataSourceListResponse>(`${this.baseUrl}/${projectId}/data-sources`);
  }

  deleteDataSource(dataSourceId: string): Observable<void> {
    return this.http.delete<void>(`${environment.apiBaseUrl}/data-sources/${dataSourceId}`);
  }
}
