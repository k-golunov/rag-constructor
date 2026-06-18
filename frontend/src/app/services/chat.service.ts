import { Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { Observable } from 'rxjs';

import { environment } from '../../environments/environment';

export interface ChatMessage {
  role: 'user' | 'assistant';
  content: string;
  created_at?: string;
}

export interface ChatResponse {
  answer: string;
  session_id: string;
  sources: string[];
}

export interface ChatHistoryResponse {
  session_id: string;
  project_id: string;
  messages: ChatMessage[];
}

@Injectable({ providedIn: 'root' })
export class ChatService {
  private readonly baseUrl = `${environment.apiBaseUrl}/projects`;

  constructor(private readonly http: HttpClient) {}

  sendMessage(projectId: string, question: string, sessionId: string | null): Observable<ChatResponse> {
    return this.http.post<ChatResponse>(`${this.baseUrl}/${projectId}/chat`, {
      question,
      session_id: sessionId,
    });
  }

  getHistory(projectId: string, sessionId: string): Observable<ChatHistoryResponse> {
    return this.http.get<ChatHistoryResponse>(`${this.baseUrl}/${projectId}/chat/${sessionId}`);
  }
}
