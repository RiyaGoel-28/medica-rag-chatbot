import * as DocumentPicker from 'expo-document-picker';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';
import { useAuth } from '@/providers/AuthProvider';
import {
  askQuestion,
  deleteDocument,
  fetchDocuments,
  ReferenceDocument,
  uploadDocument,
} from '@/lib/api';

type Message = {
  id: string;
  role: 'user' | 'assistant';
  text: string;
  sources?: string[];
};

const MAX_UPLOAD_BYTES = 15 * 1024 * 1024;

function SignInScreen() {
  const { error, isConfigured, isSigningIn, signIn } = useAuth();
  return (
    <SafeAreaView style={styles.authPage}>
      <View style={styles.authCard}>
        <Brand />
        <Text style={styles.eyebrow}>PRIVATE REFERENCE LIBRARY</Text>
        <Text style={styles.signInTitle}>Sign in to{ '\n' }<Text style={styles.titleAccent}>your library.</Text></Text>
        <Text style={styles.bodyCopy}>Explore information from your uploaded references. For educational use only, not diagnosis or treatment.</Text>
        {error ? <Text accessibilityRole="alert" style={styles.errorText}>{error}</Text> : null}
        {!isConfigured ? (
          <View style={styles.notice}>
            <Text style={styles.noticeTitle}>App setup needed</Text>
            <Text style={styles.noticeBody}>Configure the Auth0 domain, native and web client IDs, API audience, and backend URL in mobile/.env.</Text>
          </View>
        ) : (
          <ActionButton label="Continue with Auth0" onPress={signIn} loading={isSigningIn} />
        )}
        <Text style={styles.footnote}>Do not upload patient-identifying or private health information.</Text>
      </View>
    </SafeAreaView>
  );
}

function Brand() {
  return (
    <View style={styles.brandRow}>
      <View style={styles.brandMark}><Text style={styles.brandPlus}>+</Text></View>
      <Text style={styles.brandWord}>medica<Text style={styles.titleAccent}>.</Text></Text>
    </View>
  );
}

function ActionButton({ label, onPress, loading, secondary = false }: {
  label: string;
  onPress: () => void;
  loading?: boolean;
  secondary?: boolean;
}) {
  return (
    <Pressable
      accessibilityRole="button"
      disabled={loading}
      onPress={onPress}
      style={({ pressed }) => [styles.actionButton, secondary && styles.secondaryButton, pressed && styles.pressed, loading && styles.disabled]}
    >
      {loading ? <ActivityIndicator color={secondary ? '#176b55' : '#fff'} /> : <Text style={[styles.actionText, secondary && styles.secondaryText]}>{label}</Text>}
    </Pressable>
  );
}

export default function HomeScreen() {
  const { accessToken, isLoading: authLoading, signOut } = useAuth();
  const [documents, setDocuments] = useState<ReferenceDocument[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState('');
  const [statusIsError, setStatusIsError] = useState(false);
  const listRef = useRef<ScrollView>(null);

  const loadLibrary = useCallback(async () => {
    if (!accessToken) return;
    try {
      setDocuments(await fetchDocuments(accessToken));
    } catch (cause) {
      setStatus(cause instanceof Error ? cause.message : 'Could not load your library.');
      setStatusIsError(true);
    }
  }, [accessToken]);

  useEffect(() => {
    loadLibrary();
  }, [loadLibrary]);

  useEffect(() => {
    listRef.current?.scrollToEnd({ animated: true });
  }, [messages]);

  if (authLoading) {
    return <SafeAreaView style={styles.loadingPage}><ActivityIndicator size="large" color="#176b55" /></SafeAreaView>;
  }
  if (!accessToken) return <SignInScreen />;
  const token = accessToken;

  async function choosePdf() {
    if (busy) return;
    const result = await DocumentPicker.getDocumentAsync({
      type: 'application/pdf',
      copyToCacheDirectory: true,
      multiple: false,
    });
    if (result.canceled) return;
    const asset = result.assets[0];
    if (!asset.name.toLowerCase().endsWith('.pdf')) {
      setStatus('Choose a PDF file.');
      setStatusIsError(true);
      return;
    }
    if (asset.size && asset.size > MAX_UPLOAD_BYTES) {
      setStatus('PDF files must be 15 MB or smaller.');
      setStatusIsError(true);
      return;
    }
    setBusy(true);
    setStatus(`Indexing ${asset.name}...`);
    setStatusIsError(false);
    try {
      const uploaded = await uploadDocument(token, asset.uri, asset.name, asset.mimeType);
      setStatus(uploaded.message);
      await loadLibrary();
    } catch (cause) {
      setStatus(cause instanceof Error ? cause.message : 'The PDF could not be indexed.');
      setStatusIsError(true);
    } finally {
      setBusy(false);
    }
  }

  async function sendQuestion() {
    const trimmed = question.trim();
    if (!trimmed || busy) return;
    setQuestion('');
    setBusy(true);
    setStatus('');
    const userMessage: Message = { id: `${Date.now()}-user`, role: 'user', text: trimmed };
    setMessages((current) => [...current, userMessage]);
    try {
      const result = await askQuestion(token, trimmed);
      setMessages((current) => [...current, {
        id: `${Date.now()}-assistant`,
        role: 'assistant',
        text: result.answer,
        sources: result.sources,
      }]);
    } catch (cause) {
      setMessages((current) => [...current, {
        id: `${Date.now()}-error`,
        role: 'assistant',
        text: cause instanceof Error ? cause.message : 'The assistant is temporarily unavailable.',
      }]);
    } finally {
      setBusy(false);
    }
  }

  async function removeDocument(document: ReferenceDocument) {
    const confirmDelete = () => {
      setBusy(true);
      deleteDocument(token, document.document_id)
        .then(async (result) => {
          setStatus(result.message);
          setStatusIsError(false);
          await loadLibrary();
        })
        .catch((cause: unknown) => {
          setStatus(cause instanceof Error ? cause.message : 'Could not delete this document.');
          setStatusIsError(true);
        })
        .finally(() => setBusy(false));
    };
    if (Platform.OS === 'web') {
      if (window.confirm(`Delete ${document.filename} from your library?`)) confirmDelete();
      return;
    }
    Alert.alert('Delete reference?', `Remove ${document.filename} and its indexed chunks?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Delete', style: 'destructive', onPress: confirmDelete },
    ]);
  }

  return (
    <SafeAreaView style={styles.page} edges={['top', 'left', 'right']}>
      <View style={styles.topbar}>
        <Brand />
        <Pressable accessibilityRole="button" onPress={signOut} style={styles.signOutButton}>
          <Text style={styles.signOutText}>Sign out</Text>
        </Pressable>
      </View>
      <KeyboardAvoidingView style={styles.body} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView ref={listRef} style={styles.scroll} contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.intro}>
            <Text style={styles.eyebrow}>YOUR REFERENCE COMPANION</Text>
            <Text style={styles.hero}>Good questions{ '\n' }<Text style={styles.titleAccent}>start here.</Text></Text>
            <Text style={styles.bodyCopy}>Ask about information in your private, indexed medical references.</Text>
          </View>

          <View style={styles.libraryHeader}>
            <View>
              <Text style={styles.sectionEyebrow}>YOUR LIBRARY</Text>
              <Text style={styles.sectionTitle}>{documents.length} {documents.length === 1 ? 'document' : 'documents'}</Text>
            </View>
            <ActionButton label="＋ Add PDF" onPress={choosePdf} loading={busy} secondary />
          </View>
          {documents.length === 0 ? (
            <View style={styles.emptyLibrary}>
              <Text style={styles.emptyTitle}>No references yet</Text>
              <Text style={styles.emptyCopy}>Add a text-based PDF to start asking source-grounded questions.</Text>
            </View>
          ) : (
            <FlatList
              data={documents}
              keyExtractor={(item) => item.document_id}
              scrollEnabled={false}
              renderItem={({ item }) => (
                <View style={styles.documentRow}>
                  <View style={styles.pdfBadge}><Text style={styles.pdfBadgeText}>PDF</Text></View>
                  <View style={styles.documentInfo}>
                    <Text numberOfLines={1} style={styles.documentName}>{item.filename}</Text>
                    <Text style={styles.documentMeta}>{item.chunk_count} chunks · expires {new Date(item.expires_at).toLocaleDateString()}</Text>
                  </View>
                  <Pressable accessibilityRole="button" accessibilityLabel={`Delete ${item.filename}`} onPress={() => removeDocument(item)} hitSlop={8} style={styles.deleteButton}>
                    <Text style={styles.deleteText}>×</Text>
                  </Pressable>
                </View>
              )}
            />
          )}

          <View style={styles.questionSection}>
            <Text style={styles.sectionEyebrow}>REFERENCE CHAT</Text>
            {messages.length === 0 ? (
              <View style={styles.emptyChat}>
                <Text style={styles.emptyTitle}>Ask about your references</Text>
                <Text style={styles.emptyCopy}>Answers use retrieved excerpts and show source documents. Unsupported questions are declined.</Text>
              </View>
            ) : messages.map((message) => (
              <View key={message.id} style={[styles.message, message.role === 'user' ? styles.userMessage : styles.assistantMessage]}>
                <Text style={styles.messageLabel}>{message.role === 'user' ? 'YOU' : 'REFERENCE ASSISTANT'}</Text>
                <Text selectable style={styles.messageText}>{message.text}</Text>
                {message.sources?.length ? <Text style={styles.sources}><Text style={styles.sourcesLabel}>Sources: </Text>{message.sources.join(' · ')}</Text> : null}
              </View>
            ))}
            {busy && messages.length > 0 ? <ActivityIndicator style={styles.thinking} color="#176b55" /> : null}
          </View>
        </ScrollView>

        {status ? <Text accessibilityLiveRegion="polite" style={[styles.status, statusIsError && styles.errorText]}>{status}</Text> : null}
        <View style={styles.composerWrap}>
          <TextInput
            accessibilityLabel="Ask a medical information question"
            editable={!busy}
            multiline
            onChangeText={setQuestion}
            onSubmitEditing={sendQuestion}
            placeholder="Ask about your reference library..."
            placeholderTextColor="#98a29c"
            returnKeyType="send"
            style={styles.input}
            value={question}
          />
          <Pressable accessibilityRole="button" accessibilityLabel="Send question" disabled={busy || !question.trim()} onPress={sendQuestion} style={({ pressed }) => [styles.sendButton, pressed && styles.pressed, (busy || !question.trim()) && styles.disabled]}>
            {busy ? <ActivityIndicator color="#fff" size="small" /> : <Text style={styles.sendIcon}>→</Text>}
          </Pressable>
        </View>
        <Text style={styles.disclaimer}>Educational only, not diagnosis or treatment. Never upload patient-identifying details.</Text>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

const styles = StyleSheet.create({
  page: { flex: 1, backgroundColor: '#f4f5ef' },
  authPage: { flex: 1, justifyContent: 'center', alignItems: 'center', padding: 22, backgroundColor: '#e7ece4' },
  authCard: { width: '100%', maxWidth: 440, padding: 28, borderRadius: 8, backgroundColor: '#fff' },
  loadingPage: { flex: 1, justifyContent: 'center', backgroundColor: '#f4f5ef' },
  topbar: { minHeight: 62, paddingHorizontal: 20, flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', borderBottomWidth: 1, borderColor: '#e3e8e1', backgroundColor: '#fff' },
  body: { flex: 1, alignSelf: 'center', width: '100%', maxWidth: 900 },
  scroll: { flex: 1 },
  content: { paddingHorizontal: 20, paddingTop: 24, paddingBottom: 20 },
  brandRow: { flexDirection: 'row', alignItems: 'center', gap: 9 },
  brandMark: { width: 27, height: 27, borderRadius: 7, backgroundColor: '#176b55', alignItems: 'center', justifyContent: 'center' },
  brandPlus: { color: '#fff', fontSize: 22, lineHeight: 24, fontWeight: '400' },
  brandWord: { color: '#202b29', fontSize: 20, fontWeight: '800' },
  titleAccent: { color: '#176b55' },
  signOutButton: { padding: 8 },
  signOutText: { color: '#66736d', fontSize: 12, fontWeight: '600' },
  intro: { marginBottom: 25, paddingTop: 9 },
  eyebrow: { marginTop: 36, color: '#176b55', fontSize: 10, fontWeight: '700' },
  hero: { marginTop: 12, color: '#202b29', fontSize: 39, fontWeight: '700', lineHeight: 46 },
  signInTitle: { marginTop: 17, color: '#202b29', fontSize: 36, fontWeight: '700', lineHeight: 42 },
  bodyCopy: { marginTop: 12, color: '#697670', fontSize: 14, lineHeight: 22 },
  libraryHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 12, marginBottom: 10 },
  sectionEyebrow: { color: '#74857b', fontSize: 9, fontWeight: '700' },
  sectionTitle: { marginTop: 4, color: '#202b29', fontSize: 17, fontWeight: '700' },
  actionButton: { minHeight: 46, paddingHorizontal: 16, alignItems: 'center', justifyContent: 'center', borderRadius: 5, backgroundColor: '#176b55' },
  secondaryButton: { minHeight: 38, paddingHorizontal: 12, borderWidth: 1, borderColor: '#cbdccf', backgroundColor: '#f6faf5' },
  actionText: { color: '#fff', fontSize: 13, fontWeight: '700' },
  secondaryText: { color: '#105440', fontSize: 11 },
  pressed: { opacity: 0.82 },
  disabled: { opacity: 0.55 },
  emptyLibrary: { padding: 16, borderWidth: 1, borderColor: '#e3e8e1', borderRadius: 5, backgroundColor: '#fff' },
  emptyChat: { paddingVertical: 18 },
  emptyTitle: { color: '#34413b', fontSize: 13, fontWeight: '700' },
  emptyCopy: { marginTop: 5, color: '#7a867f', fontSize: 12, lineHeight: 18 },
  documentRow: { minHeight: 62, flexDirection: 'row', alignItems: 'center', gap: 11, marginBottom: 7, paddingHorizontal: 10, borderWidth: 1, borderColor: '#e3e8e1', borderRadius: 5, backgroundColor: '#fff' },
  pdfBadge: { width: 34, height: 34, alignItems: 'center', justifyContent: 'center', borderRadius: 4, backgroundColor: '#f6e9e4' },
  pdfBadgeText: { color: '#a45141', fontSize: 9, fontWeight: '800' },
  documentInfo: { flex: 1, minWidth: 0 },
  documentName: { color: '#34413b', fontSize: 12, fontWeight: '600' },
  documentMeta: { marginTop: 4, color: '#87928b', fontSize: 10 },
  deleteButton: { width: 30, height: 30, alignItems: 'center', justifyContent: 'center' },
  deleteText: { color: '#a45141', fontSize: 22, lineHeight: 25 },
  questionSection: { marginTop: 27, paddingTop: 18, borderTopWidth: 1, borderColor: '#e3e8e1' },
  message: { maxWidth: '92%', marginTop: 13, padding: 13, borderWidth: 1, borderColor: '#e3e8e1', borderRadius: 5 },
  userMessage: { alignSelf: 'flex-end', borderColor: '#d7e8df', backgroundColor: '#edf5ee' },
  assistantMessage: { alignSelf: 'flex-start', backgroundColor: '#fff' },
  messageLabel: { marginBottom: 6, color: '#839088', fontSize: 9, fontWeight: '700' },
  messageText: { color: '#34413b', fontSize: 13, lineHeight: 20 },
  sources: { marginTop: 9, color: '#77847c', fontSize: 10, lineHeight: 16 },
  sourcesLabel: { color: '#496352', fontWeight: '700' },
  thinking: { alignSelf: 'flex-start', margin: 12 },
  status: { paddingHorizontal: 20, paddingBottom: 8, color: '#176b55', fontSize: 11 },
  errorText: { color: '#a43c32' },
  notice: { marginTop: 18, padding: 13, borderLeftWidth: 2, borderColor: '#c65f4d', backgroundColor: '#fbf4f1' },
  noticeTitle: { color: '#604a42', fontSize: 12, fontWeight: '700' },
  noticeBody: { marginTop: 4, color: '#6c625e', fontSize: 11, lineHeight: 17 },
  footnote: { marginTop: 22, color: '#98a29c', fontSize: 10, lineHeight: 16 },
  composerWrap: { minHeight: 56, flexDirection: 'row', alignItems: 'center', gap: 10, marginHorizontal: 14, paddingHorizontal: 13, paddingVertical: 6, borderWidth: 1, borderColor: '#dce4dc', borderRadius: 5, backgroundColor: '#fff' },
  input: { flex: 1, minHeight: 38, maxHeight: 110, paddingVertical: 8, color: '#202b29', fontSize: 13, lineHeight: 18 },
  sendButton: { width: 38, height: 38, alignItems: 'center', justifyContent: 'center', borderRadius: 4, backgroundColor: '#176b55' },
  sendIcon: { color: '#fff', fontSize: 21, lineHeight: 25 },
  disclaimer: { paddingHorizontal: 14, paddingTop: 7, paddingBottom: 6, color: '#98a29c', fontSize: 9, textAlign: 'center' },
});