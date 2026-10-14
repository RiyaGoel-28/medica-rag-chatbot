import { Platform } from 'react-native';

export type ReferenceDocument = {
  document_id: string;
  filename: string;
  chunk_count: number;
  uploaded_at: string;
  expires_at: string;
};

export type ChatResult = {
  answer: string;
  sources: string[];
};

const configuredApiUrl = process.env.EXPO_PUBLIC_API_URL ?? '';
const apiUrl = (configuredApiUrl || (
  Platform.OS === 'android' ? 'http://10.0.2.2:5000' : 'http://127.0.0.1:5000'
)).replace(/\/$/, '');

async function request<T>(path: string, token: string, init: RequestInit = {}): Promise<T> {
  if (!apiUrl) throw new Error('Set EXPO_PUBLIC_API_URL in mobile/.env to your Flask API URL.');
  const headers = new Headers(init.headers);
  headers.set('Authorization', `Bearer ${token}`);
  const response = await fetch(`${apiUrl}${path}`, { ...init, headers });
  const result = await response.json().catch(() => ({})) as T & { error?: string };
  if (!response.ok) throw new Error(result.error || `Request failed (${response.status}).`);
  return result;
}

export async function fetchDocuments(token: string) {
  const result = await request<{ documents: ReferenceDocument[] }>('/api/documents', token);
  return result.documents;
}

export function askQuestion(token: string, question: string) {
  return request<ChatResult>('/api/chat', token, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ question }),
  });
}

export async function uploadDocument(token: string, uri: string, name: string, mimeType?: string) {
  const form = new FormData();
  if (typeof window !== 'undefined' && typeof File !== 'undefined' && uri.startsWith('blob:')) {
    const fileResponse = await fetch(uri);
    const file = await fileResponse.blob();
    form.append('file', new File([file], name, { type: mimeType || 'application/pdf' }));
  } else {
    form.append('file', { uri, name, type: mimeType || 'application/pdf' } as unknown as Blob);
  }
  return request<{ message: string; document_id: string; chunks: number }>(
    '/api/documents',
    token,
    { method: 'POST', body: form },
  );
}

export function deleteDocument(token: string, documentId: string) {
  return request<{ message: string }>(`/api/documents/${encodeURIComponent(documentId)}`, token, {
    method: 'DELETE',
  });
}