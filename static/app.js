// Shokti Client Application State
let state = {
  token: localStorage.getItem('shokti_access_token') || '',
  refreshToken: localStorage.getItem('shokti_refresh_token') || '',
  user: null,
  examsLocked: false,
  latestExamFeedback: null,
  latestExamAnswerAudit: [],
  analysisPollInterval: null,
  
  // Practice Session State
  practice: {
    sessionId: null,
    mcqs: [],
    currentIndex: 0,
    answers: [],
    questionStartTime: null,
    mode: 'adaptive'
  },
  
  // Exam Session State
  exam: {
    examId: null,
    title: '',
    mcqs: [],
    currentIndex: 0,
    answers: {}, // map of mcq_id -> selected_option
    timerInterval: null,
    secondsRemaining: 0,
    startTime: null,
    sessionId: null,
    latestAttemptId: null,
    kind: 'fixed',
    submitEndpoint: null,
    originTab: 'exams',
    lastAnswerAt: null,
    questionTiming: {},
    mcqCache: {} // Cache complete MCQ details
  },
  
  // Global Catalog
  catalog: {
    subjects: [],
    books: [],
    chapters: [],
    topics: []
  },
  
  // Charts
  learningCurveChart: null
};

// --- Initialization ---
document.addEventListener('DOMContentLoaded', () => {
  handleRouting();
  window.addEventListener('hashchange', handleRouting);
});

// --- API Helpers ---
async function apiRequest(path, method = 'GET', body = null) {
  const headers = {
    'Content-Type': 'application/json'
  };
  if (state.token) {
    headers['Authorization'] = `Bearer ${state.token}`;
  }
  
  const options = {
    method,
    headers
  };
  
  if (body) {
    options.body = JSON.stringify(body);
  }
  
  let response = await fetch(path, options);
  
  // Handle token expiration / refresh
  if (response.status === 401 && state.refreshToken) {
    const refreshed = await tryTokenRefresh();
    if (refreshed) {
      headers['Authorization'] = `Bearer ${state.token}`;
      // Need to re-create options with fresh headers
      const retryOptions = { ...options, headers };
      response = await fetch(path, retryOptions);
    } else {
      handleLogout();
      throw new Error("Session expired. Please log in again.");
    }
  }
  
  if (!response.ok) {
    const errData = await response.json().catch(() => ({}));
    throw new Error(errData.detail || `Request failed with status ${response.status}`);
  }
  
  return response.json();
}

function escapeAttribute(value) {
  return String(value || '')
    .replace(/\\/g, '\\\\')
    .replace(/'/g, "\\'")
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');
}

async function tryTokenRefresh() {
  try {
    const res = await fetch('/api/auth/refresh', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: state.refreshToken })
    });
    if (res.ok) {
      const data = await res.json();
      state.token = data.access_token;
      state.refreshToken = data.refresh_token || state.refreshToken;
      localStorage.setItem('shokti_access_token', state.token);
      localStorage.setItem('shokti_refresh_token', state.refreshToken);
      return true;
    }
  } catch (e) {
    console.error("Token refresh failed", e);
  }
  return false;
}

// --- Dynamic Scroll Utility for Landing Page ---
function scrollToSection(id) {
  const el = document.getElementById(id);
  if (el) {
    el.scrollIntoView({ behavior: 'smooth' });
  }
}

// --- View Navigation & Routing ---
function showView(viewId) {
  document.querySelectorAll('.view-panel').forEach(panel => {
    panel.classList.remove('active');
  });
  const target = document.getElementById(viewId);
  if (target) {
    target.classList.add('active');
  }
}

function navigateToView(viewName) {
  window.location.hash = `#${viewName}`;
}

async function handleRouting() {
  const hash = window.location.hash || '#landing';
  
  if (state.token) {
    // Authenticated path
    if (!state.user) {
      try {
        state.user = await apiRequest('/api/auth/me');
        document.getElementById('user-profile-name').textContent = state.user.name;
        
        // Populate profile page details
        document.getElementById('profile-user-name').textContent = state.user.name;
        document.getElementById('profile-user-email').textContent = state.user.email;
        document.getElementById('profile-avatar-initial').textContent = state.user.name.charAt(0).toUpperCase();
      } catch (e) {
        console.warn("Auth check failed on routing", e);
        handleLogout();
        return;
      }
    }

    // Always fetch exams completion status to decide if tabs are locked
    try {
      const exams = await apiRequest('/api/exams');
      const totalExams = exams.length;
      const completedExams = exams.filter(ex => ex.is_completed);
      state.examsLocked = totalExams > 0 && (completedExams.length < totalExams);
      updateNavigationTabsLock();
    } catch (err) {
      console.warn("Failed to check exams completion status on routing", err);
    }
    
    // Check if the user is on landing/auth hashes while logged in, redirect them to exams tab
    const publicHashes = ['#landing', '#login', '#register'];
    if (publicHashes.includes(hash)) {
      window.location.hash = '#exams';
      return;
    }
    
    showView('portal-view');
    
    // Route to portal tabs
    const validTabs = ['dashboard', 'practice', 'exams', 'analytics', 'profile'];
    let tab = hash.substring(1);
    if (!validTabs.includes(tab)) {
      window.location.hash = '#exams';
      return;
    }

    // Lock routing for practice and analytics if exams are locked; dashboard and exams always accessible
    if (state.examsLocked && (tab === 'practice' || tab === 'analytics')) {
      window.location.hash = '#dashboard';
      return;
    }
    
    switchPortalTab(tab);
  } else {
    // Unauthenticated path
    document.getElementById('portal-view').classList.remove('active');
    
    if (hash === '#login') {
      showView('auth-view');
      switchAuthTab('login');
    } else if (hash === '#register') {
      showView('auth-view');
      switchAuthTab('register');
    } else {
      // Default view is Landing Page
      window.location.hash = '#landing';
      showView('landing-view');
    }
  }
}

function updateNavigationTabsLock() {
  const locked = state.examsLocked;
  
  const dashboardTab = document.getElementById('nav-dashboard');
  const practiceTab = document.getElementById('nav-practice');
  const analyticsTab = document.getElementById('nav-analytics');
  
  if (dashboardTab) {
    dashboardTab.classList.remove('tab-locked');
    dashboardTab.innerHTML = 'Dashboard';
    dashboardTab.style.opacity = '1';
    dashboardTab.style.cursor = 'pointer';
  }
  if (practiceTab) {
    if (locked) {
      practiceTab.classList.add('tab-locked');
      practiceTab.innerHTML = '🔒 Practice Session';
      practiceTab.style.opacity = '0.6';
      practiceTab.style.cursor = 'not-allowed';
    } else {
      practiceTab.classList.remove('tab-locked');
      practiceTab.innerHTML = 'Practice Session';
      practiceTab.style.opacity = '1';
      practiceTab.style.cursor = 'pointer';
    }
  }
  if (analyticsTab) {
    if (locked) {
      analyticsTab.classList.add('tab-locked');
      analyticsTab.innerHTML = '🔒 Analytics';
      analyticsTab.style.opacity = '0.6';
      analyticsTab.style.cursor = 'not-allowed';
    } else {
      analyticsTab.classList.remove('tab-locked');
      analyticsTab.innerHTML = 'Analytics';
      analyticsTab.style.opacity = '1';
      analyticsTab.style.cursor = 'pointer';
    }
  }
}

function switchAuthTab(tab) {
  document.getElementById('tab-login-btn').classList.toggle('active', tab === 'login');
  document.getElementById('tab-register-btn').classList.toggle('active', tab === 'register');
  document.getElementById('login-form').classList.toggle('active', tab === 'login');
  document.getElementById('register-form').classList.toggle('active', tab === 'register');
  document.getElementById('auth-error').classList.add('hidden');
}

function switchPortalTab(tab) {
  // Lock practice/analytics if exams are locked; exams tab is always navigable
  // (Start buttons bypass switchPortalTab entirely so mock test always works)
  if (state.examsLocked && (tab === 'practice' || tab === 'analytics')) {
    alert("🔒 Complete all available fixed exams first to unlock your personalized learning workspace, adaptive practice sessions, and detailed analytics!");
    window.location.hash = '#dashboard';
    return;
  }

  document.querySelectorAll('.nav-tab').forEach(btn => {
    btn.classList.remove('active');
  });
  const navBtn = document.getElementById(`nav-${tab}`);
  if (navBtn) navBtn.classList.add('active');
  
  document.querySelectorAll('.portal-tab-content').forEach(content => {
    content.classList.remove('active');
  });
  const tabContent = document.getElementById(`tab-${tab}`);
  if (tabContent) tabContent.classList.add('active');
  
  // Refresh Tab specific data
  if (tab === 'dashboard') {
    loadDashboard();
  } else if (tab === 'practice') {
    resetPracticeTab();
    loadCatalogFilters();
  } else if (tab === 'exams') {
    resetExamsTab();
    loadExams();
  } else if (tab === 'analytics') {
    loadAnalytics();
  } else if (tab === 'profile') {
    loadProfileStats();
  }
}

// --- Auth Operations ---
async function handleLogin(e) {
  e.preventDefault();
  const email = document.getElementById('login-email').value;
  const password = document.getElementById('login-password').value;
  const errDiv = document.getElementById('auth-error');
  
  try {
    const data = await apiRequest('/api/auth/login', 'POST', { email, password });
    state.token = data.access_token;
    state.refreshToken = data.refresh_token;
    localStorage.setItem('shokti_access_token', state.token);
    localStorage.setItem('shokti_refresh_token', state.refreshToken);
    errDiv.classList.add('hidden');
    
    // Trigger router update
    window.location.hash = '#dashboard';
  } catch (err) {
    errDiv.textContent = err.message;
    errDiv.classList.remove('hidden');
  }
}

async function handleRegister(e) {
  e.preventDefault();
  const name = document.getElementById('reg-name').value;
  const email = document.getElementById('reg-email').value;
  const password = document.getElementById('reg-password').value;
  const errDiv = document.getElementById('auth-error');
  
  try {
    const data = await apiRequest('/api/auth/register', 'POST', { name, email, password });
    state.token = data.access_token;
    state.refreshToken = data.refresh_token;
    localStorage.setItem('shokti_access_token', state.token);
    localStorage.setItem('shokti_refresh_token', state.refreshToken);
    errDiv.classList.add('hidden');
    
    // Trigger router update
    window.location.hash = '#dashboard';
  } catch (err) {
    errDiv.textContent = err.message;
    errDiv.classList.remove('hidden');
  }
}

function handleLogout() {
  state.token = '';
  state.refreshToken = '';
  state.user = null;
  localStorage.removeItem('shokti_access_token');
  localStorage.removeItem('shokti_refresh_token');
  
  // Clear any timers
  if (state.exam.timerInterval) {
    clearInterval(state.exam.timerInterval);
  }
  
  // Trigger router update
  window.location.hash = '#landing';
}

// --- Dashboard Loading ---
async function loadDashboard() {
  try {
    const stats = await apiRequest('/api/student/stats');
    document.getElementById('header-streak').textContent = stats.current_streak;
    document.getElementById('header-answered').textContent = stats.total_answered;
    
    // Load Weakest topics
    const weak = await apiRequest('/api/student/weak-topics');
    const weakList = document.getElementById('weak-topics-list');
    
    if (weak && weak.length > 0) {
      weakList.innerHTML = '';
      weak.slice(0, 4).forEach(item => {
        const div = document.createElement('div');
        div.className = 'weak-topic-item';
        div.innerHTML = `
          <div class="weak-topic-meta">
            <span>${item.topic_name} (${item.chapter_name})</span>
            <span>${item.accuracy.toFixed(0)}% Accuracy</span>
          </div>
          <div class="weak-progress-bar">
            <div class="weak-progress-fill" style="width: ${item.accuracy}%"></div>
          </div>
        `;
        weakList.appendChild(div);
      });
    } else {
      weakList.innerHTML = `<p class="muted-text">Great job! No weak topics found yet. Take a diagnostic or mock exam to populate your stats.</p>`;
    }
    
    // Load Exam shortcuts
    const exams = await apiRequest('/api/exams');
    const examShortcutBox = document.getElementById('dashboard-exam-shortcuts');
    examShortcutBox.innerHTML = '';
    
    exams.slice(0, 3).forEach(ex => {
      const div = document.createElement('div');
      div.className = 'exam-shortcut';
      div.innerHTML = `
        <div class="exam-shortcut-info">
          <h4>${ex.title}</h4>
          <p>${ex.mcq_count} Questions | ${ex.duration_minutes}m</p>
        </div>
        <button class="btn primary-btn btn-sm" onclick="startShortcutExam('${ex.exam_id}')">Start</button>
      `;
      examShortcutBox.appendChild(div);
    });
    
  } catch (e) {
    console.error("Dashboard failed to load", e);
  }
}

// --- Catalog Cascading Filters ---
async function loadCatalogFilters() {
  try {
    state.catalog.subjects = await apiRequest('/api/subjects');
    const subSel = document.getElementById('practice-subject');
    subSel.innerHTML = '<option value="">All Subjects</option>';
    state.catalog.subjects.forEach(s => {
      subSel.innerHTML += `<option value="${s.id}">${s.name}</option>`;
    });
    
    // Reset dependant dropdowns
    document.getElementById('practice-book').innerHTML = '<option value="">All Books</option>';
    document.getElementById('practice-book').disabled = true;
    document.getElementById('practice-chapter').innerHTML = '<option value="">All Chapters</option>';
    document.getElementById('practice-chapter').disabled = true;
    document.getElementById('practice-topic').innerHTML = '<option value="">All Topics</option>';
    document.getElementById('practice-topic').disabled = true;
  } catch (e) {
    console.error("Catalog load failed", e);
  }
}

async function loadSubjectBooks(subjectId) {
  const bookSel = document.getElementById('practice-book');
  bookSel.innerHTML = '<option value="">All Books</option>';
  
  if (!subjectId) {
    bookSel.disabled = true;
    return;
  }
  
  try {
    state.catalog.books = await apiRequest(`/api/books?subject_id=${subjectId}`);
    state.catalog.books.forEach(b => {
      bookSel.innerHTML += `<option value="${b.id}">${b.title}</option>`;
    });
    bookSel.disabled = false;
    
    // Reset chapter/topic
    document.getElementById('practice-chapter').innerHTML = '<option value="">All Chapters</option>';
    document.getElementById('practice-chapter').disabled = true;
    document.getElementById('practice-topic').innerHTML = '<option value="">All Topics</option>';
    document.getElementById('practice-topic').disabled = true;
  } catch (e) {
    console.error("Books load failed", e);
  }
}

async function loadBookChapters(bookId) {
  const chSel = document.getElementById('practice-chapter');
  chSel.innerHTML = '<option value="">All Chapters</option>';
  
  if (!bookId) {
    chSel.disabled = true;
    return;
  }
  
  try {
    state.catalog.chapters = await apiRequest(`/api/chapters?book_id=${bookId}`);
    state.catalog.chapters.forEach(c => {
      chSel.innerHTML += `<option value="${c.chapter_id}">${c.chapter_name}</option>`;
    });
    chSel.disabled = false;
    
    document.getElementById('practice-topic').innerHTML = '<option value="">All Topics</option>';
    document.getElementById('practice-topic').disabled = true;
  } catch (e) {
    console.error("Chapters load failed", e);
  }
}

async function loadChapterTopics(chapterId) {
  const topSel = document.getElementById('practice-topic');
  topSel.innerHTML = '<option value="">All Topics</option>';
  
  if (!chapterId) {
    topSel.disabled = true;
    return;
  }
  
  try {
    state.catalog.topics = await apiRequest(`/api/topics?chapter_id=${chapterId}`);
    state.catalog.topics.forEach(t => {
      topSel.innerHTML += `<option value="${t.topic_name}">${t.topic_name}</option>`;
    });
    topSel.disabled = false;
  } catch (e) {
    console.error("Topics load failed", e);
  }
}

// --- Practice Session Runner ---
function resetPracticeTab() {
  document.getElementById('practice-setup-card').classList.remove('hidden');
  hidePracticePreparing();
}

function showPracticePreparing() {
  const overlay = document.getElementById('practice-preparing-overlay');
  if (overlay) overlay.classList.remove('hidden');
}

function hidePracticePreparing() {
  const overlay = document.getElementById('practice-preparing-overlay');
  if (overlay) overlay.classList.add('hidden');
}

async function startQuickPractice(mode) {
  switchPortalTab('practice');
  document.getElementById('practice-mode').value = mode;
  document.getElementById('practice-count').value = 10;
  await launchPracticeSession();
}

async function startMockTest() {
  try {
    const exams = await apiRequest('/api/exams');
    if (!exams || exams.length === 0) {
      alert("No fixed model tests are available yet.");
      return;
    }
    const nextExam = exams.find(ex => !ex.is_completed) || exams[0];
    // Bypass switchPortalTab (which locks when examsLocked)
    // Mock test is always startable from dashboard
    showView('portal-view');
    await startExam(nextExam.exam_id, nextExam.title);
  } catch (e) {
    alert("Failed to start mock test: " + e.message);
  }
}

async function launchPracticeSession() {
  const mode = document.getElementById('practice-mode').value;
  const count = parseInt(document.getElementById('practice-count').value);
  const chapterEl = document.getElementById('practice-chapter');
  const topicEl = document.getElementById('practice-topic');
  
  const payload = {
    mode: mode,
    count: count,
    topic_name: topicEl.value || null,
    chapter_name: chapterEl.value ? chapterEl.options[chapterEl.selectedIndex].text : null
  };
  
  try {
    showPracticePreparing();
    const sessionData = await apiRequest('/api/practice/session', 'POST', payload);
    await startPracticeExamFromSession(sessionData, mode, count);
  } catch (e) {
    alert("Failed to start session: " + e.message);
  } finally {
    hidePracticePreparing();
  }
}

async function startPracticeExamFromSession(sessionData, mode = 'adaptive', count = null) {
  if (!sessionData.mcqs || sessionData.mcqs.length === 0) {
    alert("No questions found matching your filter criteria.");
    return;
  }

  state.practice = {
    sessionId: sessionData.session_id,
    mcqs: sessionData.mcqs,
    currentIndex: 0,
    answers: [],
    mode
  };

  state.exam = {
    examId: `practice-${sessionData.session_id}`,
    title: `${mode.charAt(0).toUpperCase() + mode.slice(1)} Practice Exam`,
    mcqs: sessionData.mcqs,
    currentIndex: 0,
    answers: {},
    timerInterval: null,
    secondsRemaining: Math.max(count || sessionData.mcqs.length, sessionData.mcqs.length) * 90,
    startTime: Date.now(),
    sessionId: sessionData.session_id,
    latestAttemptId: null,
    kind: 'practice',
    submitEndpoint: `/api/practice/sessions/${sessionData.session_id}/submit`,
    originTab: 'practice',
    lastAnswerAt: Date.now(),
    questionTiming: {},
    mcqCache: {}
  };

  showExamRunner();
  document.getElementById('practice-setup-card').classList.add('hidden');

  startExamTimer();
  await loadAllExamQuestions();
}

// --- Timed Exams Tab ---
function resetExamsTab() {
  if (state.analysisPollInterval) {
    clearInterval(state.analysisPollInterval);
    state.analysisPollInterval = null;
  }
  document.getElementById('exams-list-container').classList.remove('hidden');
  document.getElementById('exam-attempts-container').classList.add('hidden');
  document.getElementById('exam-runner').classList.add('hidden');
  document.getElementById('exam-results-summary').classList.add('hidden');
  
  if (state.exam.timerInterval) {
    clearInterval(state.exam.timerInterval);
  }
}

async function loadExams() {
  try {
    const exams = await apiRequest('/api/exams');
    
    const totalExams = exams.length;
    const completedExams = exams.filter(ex => ex.is_completed);
    state.examsLocked = totalExams > 0 && (completedExams.length < totalExams);
    
    const banner = document.getElementById('exams-diagnostic-banner');
    if (banner) {
      if (state.examsLocked) {
        banner.classList.remove('hidden');
      } else {
        banner.classList.add('hidden');
      }
    }
    
    updateNavigationTabsLock();

    const container = document.getElementById('exams-grid-data');
    container.innerHTML = '';
    
    exams.forEach(ex => {
      const card = document.createElement('div');
      card.className = 'exam-card';
      
      const badgeHtml = ex.is_completed 
        ? `<span class="meta-tag success" style="margin-left: 8px; vertical-align: middle; background-color: var(--success-bg); color: var(--success); font-size: 11px; padding: 3px 8px; border-radius: 8px;">Completed</span>` 
        : ``;
        
      const btnClass = ex.is_completed ? 'btn secondary-btn btn-full' : 'btn primary-btn btn-full';
      const btnText = ex.is_completed ? 'Retry Exam' : 'Launch Exam';
      const latestScore = Number.isFinite(ex.latest_score_percentage)
        ? `<p class="exam-card-meta">Latest Score: ${ex.latest_score_percentage.toFixed(0)}% | Attempts: ${ex.attempt_count}</p>`
        : `<p class="exam-card-meta">Attempts: ${ex.attempt_count || 0}</p>`;
      
      card.innerHTML = `
        <div>
          <h3 class="exam-card-title">${ex.title}${badgeHtml}</h3>
          <p class="exam-card-meta">${ex.mcq_count} Questions | ${ex.duration_minutes} Minutes</p>
          ${latestScore}
        </div>
        <div class="exam-card-actions">
          <button class="${btnClass}" onclick="startExam('${ex.exam_id}', '${escapeAttribute(ex.title)}')">${btnText}</button>
          ${ex.is_completed ? `<button class="btn secondary-btn btn-full" onclick="showExamAttempts('${ex.exam_id}', '${escapeAttribute(ex.title)}')">View</button>` : ``}
        </div>
      `;
      container.appendChild(card);
    });
  } catch (e) {
    console.error("Exams listing failed", e);
  }
}

async function startShortcutExam(examId) {
  // Bypass switchPortalTab (which locks exams when examsLocked)
  // The mock exam is how users unlock the system — always allow starting
  showView('portal-view');
  await startExam(examId);
}

async function showExamAttempts(examId, examTitle) {
  try {
    if (state.analysisPollInterval) {
      clearInterval(state.analysisPollInterval);
      state.analysisPollInterval = null;
    }
    const attempts = await apiRequest(`/api/exams/${examId}/attempts`);
    document.getElementById('exams-list-container').classList.add('hidden');
    document.getElementById('exam-runner').classList.add('hidden');
    document.getElementById('exam-results-summary').classList.add('hidden');
    document.getElementById('exam-attempts-container').classList.remove('hidden');
    document.getElementById('attempts-exam-label').textContent = `Exam ${examId}`;
    document.getElementById('attempts-title').textContent = examTitle || 'Saved Attempts';

    const list = document.getElementById('exam-attempts-list');
    if (!attempts.length) {
      list.innerHTML = `<p class="muted-text">No saved attempts yet.</p>`;
      return;
    }

    list.innerHTML = '';
    attempts.forEach((attempt, index) => {
      const submitted = new Date(attempt.submitted_at).toLocaleString();
      const item = document.createElement('div');
      item.className = 'attempt-item';
      item.innerHTML = `
        <div>
          <strong>Attempt ${attempts.length - index}</strong>
          <p>${attempt.correct}/${attempt.total} correct | ${attempt.score_percentage.toFixed(0)}% | ${attempt.time_taken_seconds}s</p>
          <span class="muted-text">${submitted} | Analysis: ${attempt.feedback_status}</span>
        </div>
        <button class="btn primary-btn btn-sm" onclick="openSavedAttempt('${attempt.attempt_id}')">View</button>
      `;
      list.appendChild(item);
    });
  } catch (e) {
    alert("Failed to load saved attempts: " + e.message);
  }
}

async function openSavedAttempt(attemptId) {
  try {
    const attempt = await apiRequest(`/api/exams/attempts/${attemptId}`);
    state.exam = {
      examId: attempt.exam_id,
      title: attempt.exam_title || 'Saved Attempt',
      mcqs: [],
      currentIndex: 0,
      answers: {},
      timerInterval: null,
      secondsRemaining: 0,
      startTime: null,
    sessionId: attempt.session_id,
    latestAttemptId: attempt.attempt_id,
    kind: attempt.exam_id && attempt.exam_id.startsWith('practice-') ? 'practice' : 'fixed',
    submitEndpoint: null,
    originTab: attempt.exam_id && attempt.exam_id.startsWith('practice-') ? 'practice' : 'exams',
    lastAnswerAt: null,
    questionTiming: {},
    mcqCache: {}
    };
    document.getElementById('exam-attempts-container').classList.add('hidden');
    document.getElementById('exam-results-summary').classList.remove('hidden');
    renderExamResult(attempt, attempt.time_taken_seconds || 0);
    if (!attempt.feedback) {
      beginFeedbackPolling(attempt.attempt_id);
    }
  } catch (e) {
    alert("Failed to open saved attempt: " + e.message);
  }
}

async function startExam(examId, examTitle = null) {
  try {
    const startData = await apiRequest(`/api/exams/${examId}/start`, 'POST');
    
    state.exam = {
      examId: examId,
      title: examTitle || `Mock Test ${examId}`,
      mcqs: startData.mcqs,
      currentIndex: 0,
      answers: {},
      timerInterval: null,
      secondsRemaining: startData.duration_minutes * 60,
      startTime: Date.now(),
    sessionId: startData.session_id,
    latestAttemptId: null,
    kind: 'fixed',
    submitEndpoint: `/api/exams/${examId}/submit`,
    originTab: 'exams',
    lastAnswerAt: Date.now(),
    questionTiming: {},
    mcqCache: {} // Reset Cache
    };
    
    showExamRunner();
    
    startExamTimer();
    await loadAllExamQuestions();
  } catch (e) {
    alert("Failed to start exam: " + e.message);
  }
}

function getExamUiElements() {
  ensureExamWorkspace();
  return {
    examsTab: document.getElementById('tab-exams'),
    list: document.getElementById('exams-list-container'),
    attempts: document.getElementById('exam-attempts-container'),
    runner: document.getElementById('exam-runner'),
    results: document.getElementById('exam-results-summary'),
    title: document.getElementById('exam-runner-title'),
    timer: document.getElementById('exam-timer'),
    counter: document.getElementById('exam-counter'),
    progress: document.getElementById('exam-progress-fill'),
    questions: document.getElementById('exam-scrollable-questions-container'),
  };
}

function ensureExamWorkspace() {
  const examsTab = document.getElementById('tab-exams');
  if (!examsTab) return;

  if (!document.getElementById('exams-list-container')) {
    examsTab.insertAdjacentHTML('afterbegin', `<div id="exams-list-container" class="hidden"></div>`);
  }
  if (!document.getElementById('exam-attempts-container')) {
    examsTab.insertAdjacentHTML('beforeend', `<div id="exam-attempts-container" class="content-card hidden"></div>`);
  }

  const runner = document.getElementById('exam-runner');
  const runnerNeedsBuild = !runner || !document.getElementById('exam-runner-title') || !document.getElementById('exam-scrollable-questions-container');
  const runnerMarkup = `
    <div class="runner-header" style="position: sticky; top: 0; background-color: var(--surface); z-index: 10; padding: 12px 0; border-bottom: 1px solid var(--border-color); margin-bottom: 20px;">
      <span class="session-badge" id="exam-runner-title">Diagnostic Exam 1</span>
      <span class="timer-badge" id="exam-timer">Time Left: 30:00</span>
      <span class="question-progress" id="exam-counter">0 of 30 Answered</span>
    </div>
    <div class="progress-bar-container" style="margin-bottom: 24px;">
      <div class="progress-bar-fill" id="exam-progress-fill"></div>
    </div>
    <div id="exam-scrollable-questions-container" class="exam-scrollable-container" style="display: flex; flex-direction: column; gap: 30px;"></div>
    <div class="exam-nav-actions" style="justify-content: center; margin-top: 36px; border-top: 1px solid var(--border-color); padding-top: 24px;">
      <button class="btn success-btn" id="exam-submit-btn" onclick="confirmSubmitExam()" style="width: 100%; max-width: 400px; font-size: 16px; padding: 14px 28px; border-radius: var(--border-radius-sm);">Submit Exam</button>
    </div>
  `;
  if (!runner) {
    examsTab.insertAdjacentHTML('beforeend', `<div id="exam-runner" class="content-card hidden readable-width-container">${runnerMarkup}</div>`);
  } else if (runnerNeedsBuild) {
    runner.innerHTML = runnerMarkup;
  }

  const results = document.getElementById('exam-results-summary');
  const resultsNeedsBuild = !results || !document.getElementById('exam-res-score') || !document.getElementById('exam-analysis-drawer');
  const resultsMarkup = `
    <div class="exam-results-layout">
      <div class="exam-results-main">
        <div class="summary-congrats">
          <span class="congrats-emoji">📊</span>
          <h2 id="exam-result-title">Exam Results</h2>
          <p>Review your score, answer choices, and related practice.</p>
        </div>
        <div class="summary-stats-row">
          <div class="summary-stat-box"><span class="stat-val" id="exam-res-score">0/0</span><span class="stat-lbl">Final Score</span></div>
          <div class="summary-stat-box"><span class="stat-val" id="exam-res-pct">0%</span><span class="stat-lbl">Percentage</span></div>
          <div class="summary-stat-box"><span class="stat-val" id="exam-res-time">0s</span><span class="stat-lbl">Total Time</span></div>
        </div>
        <div class="summary-section">
          <h3>Per-Topic Accuracy</h3>
          <div class="summary-topic-bars" id="exam-res-topic-breakdown"></div>
        </div>
        <div class="review-answers-box">
          <h3>Detailed Question Review</h3>
          <p class="muted-text">Incorrect questions display related mock practice questions to try.</p>
          <div id="exam-res-questions-review"></div>
        </div>
        <div class="summary-actions" style="margin-top: 30px;">
          <button class="btn primary-btn" id="exam-results-primary-action" onclick="resetExamsTab()">Back to Model Tests</button>
          <button class="btn secondary-btn" id="exam-results-secondary-action" onclick="switchPortalTab('dashboard')">Back to Dashboard</button>
        </div>
      </div>
      <aside id="exam-analysis-drawer" class="analysis-drawer">
        <div class="analysis-drawer-header">
          <h3>AI Tutor Insights</h3>
          <span class="analysis-status-pill" id="analysis-status-pill">Loading</span>
        </div>
        <div class="analysis-drawer-body">
          <div id="drawer-loading-view" class="drawer-loading-card">
            <div class="drawer-loading-content">
              <div class="tutor-loading-spinner"></div>
              <h4>Generating Analysis</h4>
              <p>Results are ready. Gemini analysis is being stored and will appear here automatically.</p>
            </div>
          </div>
          <div id="drawer-analysis-view" class="hidden">
            <div class="drawer-summary-card"><h4>Quantitative Performance Summary</h4><p id="drawer-feedback-summary">Summary text goes here...</p></div>
            <div class="drawer-list-card weak-card"><h5>Concepts Needing Fixes</h5><ul id="drawer-feedback-weak"></ul></div>
            <div class="drawer-list-card strong-card"><h5>Key Strengths & Praise</h5><ul id="drawer-feedback-strong"></ul></div>
            <div class="drawer-list-card plan-card"><h5>Personalized Study Plan</h5><ul id="drawer-feedback-tips"></ul></div>
          </div>
        </div>
      </aside>
    </div>
  `;
  if (!results) {
    examsTab.insertAdjacentHTML('beforeend', `<div id="exam-results-summary" class="content-card hidden">${resultsMarkup}</div>`);
  } else if (resultsNeedsBuild) {
    results.innerHTML = resultsMarkup;
  }
}

function assertExamUiReady() {
  const ui = getExamUiElements();
  const missing = Object.entries(ui)
    .filter(([, el]) => !el)
    .map(([name]) => name);
  if (missing.length > 0) {
    throw new Error(`Exam screen is missing required UI elements: ${missing.join(', ')}. Please hard refresh the page and try again.`);
  }
  return ui;
}

function showExamRunner() {
  const ui = assertExamUiReady();
  switchPortalTab('exams');
  ui.title.textContent = state.exam.title.toUpperCase();
  ui.list.classList.add('hidden');
  ui.attempts.classList.add('hidden');
  ui.results.classList.add('hidden');
  ui.runner.classList.remove('hidden');
}

function startExamTimer() {
  if (state.exam.timerInterval) {
    clearInterval(state.exam.timerInterval);
  }
  
  const { timer: timerLabel } = assertExamUiReady();
  const setTimerLabel = () => {
    const mins = Math.floor(state.exam.secondsRemaining / 60);
    const secs = state.exam.secondsRemaining % 60;
    timerLabel.textContent = `Time Left: ${mins}:${secs < 10 ? '0' : ''}${secs}`;
  };
  setTimerLabel();
  
  state.exam.timerInterval = setInterval(() => {
    state.exam.secondsRemaining--;
    if (state.exam.secondsRemaining <= 0) {
      clearInterval(state.exam.timerInterval);
      alert("Time is up! Submitting exam automatically.");
      submitExam();
    } else {
      setTimerLabel();
    }
  }, 1000);
}

async function loadAllExamQuestions() {
  const container = document.getElementById('exam-scrollable-questions-container');
  if (!container) return;
  
  container.innerHTML = '<div style="text-align: center; padding: 20px; font-weight: 500; color: var(--text-muted);">Loading exam questions...</div>';
  
  try {
    const promises = state.exam.mcqs.map(async (brief, index) => {
      let mcq = state.exam.mcqCache[brief.id];
      if (!mcq) {
        mcq = await apiRequest(`/api/mcqs/${brief.id}`);
        state.exam.mcqCache[brief.id] = mcq;
      }
      return { mcq, index };
    });
    
    const results = await Promise.all(promises);
    container.innerHTML = '';
    
    // Update progress tracker
    updateExamProgress();
    
    results.forEach(({ mcq, index }) => {
      const qCard = document.createElement('div');
      qCard.className = 'content-card';
      qCard.style.padding = '24px';
      qCard.style.margin = '0';
      qCard.style.border = '1px solid var(--border-color)';
      qCard.style.borderRadius = 'var(--border-radius-sm)';
      qCard.style.backgroundColor = 'var(--surface-tonal)';
      
      const selected = state.exam.answers[mcq.id] || '';
      
      let optionsHtml = '';
      ['A', 'B', 'C', 'D'].forEach(key => {
        const text = mcq.options[key] || '';
        if (text) {
          optionsHtml += `
            <button class="option-btn exam-opt-btn-${mcq.id} ${selected === key ? 'selected' : ''}" 
                    onclick="selectExamScrollOption(${mcq.id}, '${key}')" 
                    id="exam-opt-${mcq.id}-${key}"
                    style="margin-bottom: 8px; width: 100%;">
              <span class="opt-letter">${key}</span>
              <span class="opt-text">${text}</span>
            </button>
          `;
        }
      });
      
      qCard.innerHTML = `
        <div class="runner-header" style="border-bottom: none; margin-bottom: 12px; padding: 0;">
          <span class="session-badge" style="font-size: 13px;">Question ${index + 1}</span>
          <span class="meta-tag difficulty-tag ${mcq.difficulty}">${mcq.difficulty.toUpperCase()}</span>
        </div>
        <h3 class="question-text" style="font-size: 17px; margin-bottom: 16px; font-family: 'Outfit', sans-serif; font-weight: 600; color: var(--text-main); line-height: 1.4;">${mcq.question}</h3>
        <div class="options-grid" style="display: flex; flex-direction: column; gap: 8px;">
          ${optionsHtml}
        </div>
      `;
      container.appendChild(qCard);
    });
    
  } catch (e) {
    container.innerHTML = `<div style="text-align: center; color: var(--danger); font-weight: 600; padding: 20px;">Failed to load questions: ${e.message}</div>`;
  }
}

function selectExamScrollOption(mcqId, key) {
  const now = Date.now();
  if (!state.exam.questionTiming) {
    state.exam.questionTiming = {};
  }
  if (!state.exam.lastAnswerAt) {
    state.exam.lastAnswerAt = state.exam.startTime || now;
  }
  if (state.exam.questionTiming[mcqId] == null) {
    state.exam.questionTiming[mcqId] = Math.max(1, Math.round((now - state.exam.lastAnswerAt) / 1000));
    state.exam.lastAnswerAt = now;
  }
  state.exam.answers[mcqId] = key;
  
  // Highlight visually within this specific question card
  document.querySelectorAll(`.exam-opt-btn-${mcqId}`).forEach(btn => {
    btn.classList.remove('selected');
  });
  const selectedBtn = document.getElementById(`exam-opt-${mcqId}-${key}`);
  if (selectedBtn) {
    selectedBtn.classList.add('selected');
  }
  
  updateExamProgress();
}

function updateExamProgress() {
  const total = state.exam.mcqs.length;
  const answered = Object.keys(state.exam.answers).length;
  
  document.getElementById('exam-counter').textContent = `${answered} of ${total} Answered`;
  document.getElementById('exam-progress-fill').style.width = `${(answered / total) * 100}%`;
}

function confirmSubmitExam() {
  const total = state.exam.mcqs.length;
  const answered = Object.keys(state.exam.answers).length;
  if (answered === total) {
    submitExam();
    return;
  }

  if (confirm(`You have answered ${answered} out of ${total} questions. Are you sure you want to submit?`)) {
    submitExam();
  }
}

async function submitExam() {
  if (state.exam.timerInterval) {
    clearInterval(state.exam.timerInterval);
  }
  
  const answers = state.exam.mcqs.map(q => ({
    mcq_id: q.id,
    selected_option: state.exam.answers[q.id] || '',
    time_spent_seconds: state.exam.questionTiming && state.exam.questionTiming[q.id]
      ? state.exam.questionTiming[q.id]
      : 0
  }));
  const timeTaken = Math.round((Date.now() - state.exam.startTime) / 1000);
  const payload = {
    session_id: state.exam.sessionId,
    time_taken_seconds: timeTaken,
    answers
  };
  
  // Show the results shell immediately; the API returns scoring before AI analysis finishes.
  document.getElementById('exam-runner').classList.add('hidden');
  document.getElementById('exam-attempts-container').classList.add('hidden');
  document.getElementById('exam-results-summary').classList.remove('hidden');
  document.getElementById('exam-res-score').textContent = '--/--';
  document.getElementById('exam-res-pct').textContent = '...';
  document.getElementById('exam-res-time').textContent = `${timeTaken}s`;
  document.getElementById('exam-res-topic-breakdown').innerHTML = `<div class="loading-placeholder"><p>Calculating topic accuracies...</p></div>`;
  document.getElementById('exam-res-questions-review').innerHTML = `<div class="loading-placeholder"><p>Loading question review...</p></div>`;
  setAnalysisLoadingState();
  renderExamResult(buildInstantExamResult(answers, timeTaken), timeTaken);

  try {
    const submitEndpoint = state.exam.submitEndpoint || `/api/exams/${state.exam.examId}/submit`;
    const res = await apiRequest(submitEndpoint, 'POST', payload);
    state.exam.latestAttemptId = res.attempt_id;
    renderExamResult(res, timeTaken);
    beginFeedbackPolling(res.attempt_id);
    
    // Re-check exams completion status to unlock tabs
    try {
      const exams = await apiRequest('/api/exams');
      const totalExams = exams.length;
      const completedExams = exams.filter(ex => ex.is_completed);
      state.examsLocked = totalExams > 0 && (completedExams.length < totalExams);
      updateNavigationTabsLock();
    } catch (err) {
      console.warn("Failed to check exams completion status on submit", err);
    }
    
  } catch (err) {
    alert("Exam submission failed: " + err.message);
    console.error(err);
    
    // Return to runner on error
    document.getElementById('exam-runner').classList.remove('hidden');
    document.getElementById('exam-results-summary').classList.add('hidden');
    setAnalysisLoadingState();
  }
}

function buildInstantExamResult(answers, timeTaken) {
  const details = [];
  const topicMap = {};
  let correct = 0;

  answers.forEach(answer => {
    const mcq = state.exam.mcqCache[answer.mcq_id] || state.exam.mcqs.find(q => q.id === answer.mcq_id) || {};
    const correctOption = (mcq.correct_answer && mcq.correct_answer.option) || 'A';
    const selectedOption = answer.selected_option || '';
    const isCorrect = selectedOption.toUpperCase() === correctOption.toUpperCase();
    if (isCorrect) correct++;

    const related = !isCorrect
      ? ((mcq.practice_related_questions && mcq.practice_related_questions.length)
          ? mcq.practice_related_questions
          : buildCachedRelatedPractice(mcq, answer.mcq_id))
      : [];

    details.push({
      mcq_id: answer.mcq_id,
      selected_option: selectedOption,
      correct_option: correctOption,
      is_correct: isCorrect,
      time_spent_seconds: answer.time_spent_seconds || 0,
      practice_related_questions: related
    });

    const chapter = mcq.chapter_name || 'Unknown';
    const topic = mcq.topic_name || 'General';
    const key = `${chapter}::${topic}`;
    if (!topicMap[key]) {
      topicMap[key] = { chapter, topic, total: 0, correct: 0 };
    }
    topicMap[key].total++;
    if (isCorrect) topicMap[key].correct++;
  });

  const total = details.length;
  return {
    attempt_id: null,
    exam_id: state.exam.examId,
    exam_title: state.exam.title,
    session_id: state.exam.sessionId,
    time_taken_seconds: timeTaken,
    total,
    correct,
    score_percentage: total ? (correct / total) * 100 : 0,
    details,
    topic_breakdown: Object.values(topicMap),
    feedback_status: 'pending',
    feedback: null
  };
}

function buildCachedRelatedPractice(mcq, mcqId) {
  const topicName = mcq.topic_name || '';
  return (state.exam.mcqs || [])
    .filter(item => item.id !== mcqId && (!topicName || item.topic_name === topicName))
    .slice(0, 3)
    .map(item => {
      const cached = state.exam.mcqCache[item.id];
      return cached && cached.question ? cached.question : `${item.topic_name || 'Related'} practice question #${item.id}`;
    });
}

function renderExamResult(result, fallbackTime = 0) {
  const timeTaken = result.time_taken_seconds || fallbackTime || 0;
  document.getElementById('exam-result-title').textContent = result.exam_title || state.exam.title || 'Exam Results';
  document.getElementById('exam-res-score').textContent = `${result.correct}/${result.total}`;
  document.getElementById('exam-res-pct').textContent = `${result.score_percentage.toFixed(0)}%`;
  document.getElementById('exam-res-time').textContent = `${timeTaken}s`;

  renderTopicBreakdown(result);
  renderQuestionReview(result.details || []);
  configureResultActions();

  if (result.feedback) {
    state.latestExamFeedback = result.feedback;
    populateDrawerContent(result.feedback);
    showAnalysisReady();
  } else {
    setAnalysisLoadingState();
  }
}

function configureResultActions() {
  const primary = document.getElementById('exam-results-primary-action');
  const secondary = document.getElementById('exam-results-secondary-action');
  if (!primary || !secondary) return;

  if (state.exam.kind === 'practice') {
    primary.textContent = 'Start New Practice';
    primary.onclick = () => {
      resetPracticeTab();
      switchPortalTab('practice');
    };
    secondary.textContent = 'Back to Dashboard';
    secondary.onclick = () => switchPortalTab('dashboard');
    return;
  }

  primary.textContent = 'Back to Model Tests';
  primary.onclick = () => resetExamsTab();
  secondary.textContent = 'Back to Dashboard';
  secondary.onclick = () => switchPortalTab('dashboard');
}

function renderTopicBreakdown(result) {
  const breakdown = document.getElementById('exam-res-topic-breakdown');
  breakdown.innerHTML = '';

  const topicRows = result.topic_breakdown && result.topic_breakdown.length
    ? result.topic_breakdown.map(item => ({
        topic: item.topic || 'General',
        total: item.total || 0,
        correct: item.correct || 0
      }))
    : buildTopicBreakdownFromDetails(result.details || []);

  if (!topicRows.length) {
    breakdown.innerHTML = `<p class="muted-text">No detailed topic breakdown available.</p>`;
    return;
  }

  topicRows.forEach(item => {
    const pct = item.total ? Math.round((item.correct / item.total) * 100) : 0;
    const div = document.createElement('div');
    div.className = 'summary-topic-bar-item';
    div.innerHTML = `
      <span class="topic-bar-label">${item.topic}</span>
      <div style="flex: 1; margin: 0 20px;">
        <div class="weak-progress-bar">
          <div class="weak-progress-fill" style="width: ${pct}%; background-color: ${pct >= 60 ? 'var(--success)' : 'var(--danger)'}"></div>
        </div>
      </div>
      <span class="topic-bar-percent">${item.correct}/${item.total} (${pct}%)</span>
    `;
    breakdown.appendChild(div);
  });
}

function buildTopicBreakdownFromDetails(details) {
  const topicsAcc = {};
  details.forEach(item => {
    const mcq = state.exam.mcqCache[item.mcq_id] || state.exam.mcqs.find(q => q.id === item.mcq_id) || {};
    const topicName = mcq.topic_name || 'General';
    if (!topicsAcc[topicName]) {
      topicsAcc[topicName] = { topic: topicName, total: 0, correct: 0 };
    }
    topicsAcc[topicName].total++;
    if (item.is_correct) topicsAcc[topicName].correct++;
  });
  return Object.values(topicsAcc);
}

function renderQuestionReview(details) {
  const reviewContainer = document.getElementById('exam-res-questions-review');
  reviewContainer.innerHTML = '';

  if (!details.length) {
    reviewContainer.innerHTML = `<p class="muted-text">No answer details are available for this attempt.</p>`;
    return;
  }

  details.forEach(item => {
    const qCard = document.createElement('div');
    qCard.className = `review-question-card ${item.is_correct ? 'correct-review' : 'wrong-review'}`;
    qCard.id = `review-q-${item.mcq_id}`;
    const timeSpentText = formatQuestionTime(item.time_spent_seconds || 0);
    let html = `
      <div class="review-question-meta">
        <strong>Question ID: ${item.mcq_id}</strong>
        <div class="review-question-badges">
          <span class="review-time-badge">Time spent: ${timeSpentText}</span>
          <span style="color: ${item.is_correct ? 'var(--success)' : 'var(--danger)'}; font-weight: bold;">
            ${item.is_correct ? 'Correct' : 'Incorrect'}
          </span>
        </div>
      </div>
    `;

    const cached = state.exam.mcqCache[item.mcq_id];
    if (cached) {
      html += renderReviewQuestionDetails(cached, item);
    } else {
      html += `
        <div class="loading-placeholder" id="placeholder-${item.mcq_id}">
          <p style="font-style: italic; color: var(--text-muted);">Loading question details...</p>
        </div>
      `;
      fetchMCQForReview(item.mcq_id, item);
    }

    qCard.innerHTML = html;
    reviewContainer.appendChild(qCard);
  });
}

function formatQuestionTime(seconds) {
  const totalSeconds = Math.max(0, Math.round(Number(seconds) || 0));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const remainder = totalSeconds % 60;
  return `${minutes}m ${remainder}s`;
}

function setAnalysisLoadingState() {
  if (state.analysisPollInterval) {
    clearInterval(state.analysisPollInterval);
    state.analysisPollInterval = null;
  }
  const pill = document.getElementById('analysis-status-pill');
  const loadingView = document.getElementById('drawer-loading-view');
  const analysisView = document.getElementById('drawer-analysis-view');
  if (pill) {
    pill.textContent = 'Loading';
    pill.classList.remove('ready');
  }
  if (loadingView) loadingView.classList.remove('hidden');
  if (analysisView) analysisView.classList.add('hidden');
}

function showAnalysisReady() {
  const pill = document.getElementById('analysis-status-pill');
  const loadingView = document.getElementById('drawer-loading-view');
  const analysisView = document.getElementById('drawer-analysis-view');
  if (pill) {
    pill.textContent = 'Ready';
    pill.classList.add('ready');
  }
  if (loadingView) loadingView.classList.add('hidden');
  if (analysisView) analysisView.classList.remove('hidden');
}

function beginFeedbackPolling(attemptId) {
  if (!attemptId) {
    populateDrawerContent(buildLocalExamFeedback({ total: 0, correct: 0, score_percentage: 0, details: [] }));
    showAnalysisReady();
    return;
  }

  setAnalysisLoadingState();
  const poll = async () => {
    try {
      const data = await apiRequest(`/api/exams/attempts/${attemptId}/feedback`);
      if (data.feedback_status === 'ready' && data.feedback) {
        state.latestExamFeedback = data.feedback;
        populateDrawerContent(data.feedback);
        showAnalysisReady();
        if (state.analysisPollInterval) {
          clearInterval(state.analysisPollInterval);
          state.analysisPollInterval = null;
        }
      }
    } catch (err) {
      console.warn("Feedback polling failed", err);
    }
  };
  poll();
  state.analysisPollInterval = setInterval(poll, 2000);
}

function populateDrawerContent(feedback) {
  if (!feedback) {
    feedback = buildLocalExamFeedback({ total: 0, correct: 0, score_percentage: 0, details: [] });
  }
  
  document.getElementById('drawer-feedback-summary').textContent = feedback.overall_summary || 'No overall summary provided.';
  
  const weakList = document.getElementById('drawer-feedback-weak');
  weakList.innerHTML = '';
  if (feedback.weak_topics && feedback.weak_topics.length > 0) {
    feedback.weak_topics.forEach(w => {
      const li = document.createElement('li');
      li.innerHTML = `<strong>${w.topic_name}</strong> (${w.chapter_name}) — Accuracy: ${w.accuracy_percentage.toFixed(0)}%<br>
                      <span style="color: var(--text-muted); font-size: 13px;">💡 Recommendations:</span>
                      <ul style="padding-left: 15px; margin-top: 4px; list-style-type: circle;">
                        ${w.focus_recommendations.map(rec => `<li>${rec}</li>`).join('')}
                      </ul>`;
      weakList.appendChild(li);
    });
  } else {
    weakList.innerHTML = '<li>None detected</li>';
  }
  
  const strongList = document.getElementById('drawer-feedback-strong');
  strongList.innerHTML = '';
  if (feedback.strong_topics && feedback.strong_topics.length > 0) {
    feedback.strong_topics.forEach(s => {
      const li = document.createElement('li');
      li.innerHTML = `<strong>${s.topic_name}</strong> (${s.chapter_name}) — Accuracy: ${s.accuracy_percentage.toFixed(0)}%<br>
                      <span style="color: var(--success); font-style: italic; font-size: 13px;">✨ "${s.encouragement}"</span>`;
      strongList.appendChild(li);
    });
  } else {
    strongList.innerHTML = '<li>None detected</li>';
  }
  
  const tipsList = document.getElementById('drawer-feedback-tips');
  tipsList.innerHTML = '';
  if (feedback.personalized_study_recommendations && feedback.personalized_study_recommendations.length > 0) {
    feedback.personalized_study_recommendations.forEach(tip => {
      const li = document.createElement('li');
      li.textContent = tip;
      tipsList.appendChild(li);
    });
  } else {
    tipsList.innerHTML = '<li>No recommendations available.</li>';
  }
}

function buildLocalExamFeedback(result) {
  const total = result.total || 0;
  const correct = result.correct || 0;
  const pct = Number.isFinite(result.score_percentage) ? result.score_percentage : 0;
  const topicStats = {};

  (result.details || []).forEach(item => {
    const mcq = state.exam.mcqCache[item.mcq_id] || state.exam.mcqs.find(q => q.id === item.mcq_id) || {};
    const topicName = mcq.topic_name || 'General';
    const chapterName = mcq.chapter_name || 'Unknown';
    const key = `${chapterName}::${topicName}`;
    if (!topicStats[key]) {
      topicStats[key] = { topic_name: topicName, chapter_name: chapterName, total: 0, correct: 0 };
    }
    topicStats[key].total += 1;
    if (item.is_correct) {
      topicStats[key].correct += 1;
    }
  });

  const weak_topics = [];
  const strong_topics = [];
  Object.values(topicStats).forEach(topic => {
    const accuracy = topic.total ? (topic.correct / topic.total) * 100 : 0;
    if (accuracy < 60) {
      weak_topics.push({
        topic_name: topic.topic_name,
        chapter_name: topic.chapter_name,
        accuracy_percentage: accuracy,
        focus_recommendations: [
          'Review the core definition and diagrams for this concept.',
          'Redo each missed question and explain why the correct option wins.',
          'Practice related MCQs before starting the next exam.'
        ]
      });
    } else {
      strong_topics.push({
        topic_name: topic.topic_name,
        chapter_name: topic.chapter_name,
        accuracy_percentage: accuracy,
        encouragement: 'You are answering this topic reliably. Keep it active with short reviews.'
      });
    }
  });

  weak_topics.sort((a, b) => a.accuracy_percentage - b.accuracy_percentage);
  strong_topics.sort((a, b) => b.accuracy_percentage - a.accuracy_percentage);

  let summary = `You answered ${correct}/${total} correctly (${Math.round(pct)}%). `;
  if (total === 0) {
    summary = 'No answers were submitted, so there is not enough data to analyze this exam.';
  } else if (pct >= 80) {
    summary += 'Strong result. Use the review below to clean up the remaining mistakes.';
  } else if (pct >= 60) {
    summary += 'Solid foundation. The fastest improvement is in the weak topics below.';
  } else {
    summary += 'This is useful diagnostic data. Start with wrong answers, then drill the related practice questions.';
  }

  return {
    overall_summary: summary,
    weak_topics: weak_topics.slice(0, 5),
    strong_topics: strong_topics.slice(0, 5),
    personalized_study_recommendations: [
      'Review every incorrect answer before leaving this screen.',
      'Write a one-line reason for each correct option.',
      'Use related practice questions for the topics you missed.',
      'Reattempt the wrong questions after a short break.',
      'Keep strong topics in spaced review so they stay stable.'
    ]
  };
}

function renderReviewQuestionDetails(mcq, item) {
  let explanationHtml = '';
  if (mcq.explanation) {
    explanationHtml = `
      <div style="margin-top: 10px; padding: 10px; background-color: var(--surface-card); border-radius: 6px; font-size: 14px;">
        <strong>Explanation:</strong> ${mcq.explanation}
      </div>
    `;
  }
  
  let prqHtml = '';
  if (!item.is_correct && item.practice_related_questions && item.practice_related_questions.length > 0) {
    prqHtml = `
      <div class="review-prq-list">
        <h5>📚 Related Practice Questions to Review:</h5>
        <ul>
          ${item.practice_related_questions.map(qText => `<li>${qText}</li>`).join('')}
        </ul>
      </div>
    `;
  }
  
  return `
    <div style="font-weight: 600; font-size: 16px; margin-bottom: 12px; line-height: 1.5;">${mcq.question}</div>
    <div style="display: grid; grid-template-columns: 1fr; gap: 8px; font-size: 14px; margin-bottom: 12px;">
      ${Object.entries(mcq.options).map(([key, text]) => {
        let style = 'padding: 8px 12px; border-radius: 6px; background-color: var(--bg); border: 1px solid var(--border-color);';
        let label = '';
        if (key === mcq.correct_answer.option) {
          style = 'padding: 8px 12px; border-radius: 6px; background-color: var(--success-bg); border: 1px solid var(--success); font-weight: 600;';
          label = ' <span style="color: var(--success); font-size: 12px;">(Correct Answer)</span>';
        } else if (key === item.selected_option && !item.is_correct) {
          style = 'padding: 8px 12px; border-radius: 6px; background-color: var(--danger-bg); border: 1px solid var(--danger);';
          label = ' <span style="color: var(--danger); font-size: 12px;">(Your Answer)</span>';
        } else if (key === item.selected_option && item.is_correct) {
          style = 'padding: 8px 12px; border-radius: 6px; background-color: var(--success-bg); border: 1px solid var(--success); font-weight: 600;';
          label = ' <span style="color: var(--success); font-size: 12px;">(Your Answer)</span>';
        }
        return `<div style="${style}"><strong>${key}:</strong> ${text}${label}</div>`;
      }).join('')}
    </div>
    ${explanationHtml}
    ${prqHtml}
  `;
}

async function fetchMCQForReview(mcqId, item) {
  try {
    const mcq = await apiRequest(`/api/mcqs/${mcqId}`);
    state.exam.mcqCache[mcqId] = mcq;
    const placeholder = document.getElementById(`placeholder-${mcqId}`);
    if (placeholder) {
      const card = placeholder.parentElement;
      placeholder.remove();
      const detailHtml = renderReviewQuestionDetails(mcq, item);
      card.innerHTML += detailHtml;
    }
  } catch (e) {
    console.error("Failed to load mcq details for review", e);
    const placeholder = document.getElementById(`placeholder-${mcqId}`);
    if (placeholder) {
      placeholder.innerHTML = `<p style="color: var(--danger); font-style: italic;">Failed to load question details.</p>`;
    }
  }
}

// --- Profile Subview ---
async function loadProfileStats() {
  try {
    const stats = await apiRequest('/api/student/stats');
    document.getElementById('profile-stat-streak').textContent = stats.current_streak;
    document.getElementById('profile-stat-answered').textContent = stats.total_answered;
    document.getElementById('profile-stat-time').textContent = `${stats.avg_time_seconds.toFixed(1)}s`;
    document.getElementById('profile-stat-exams').textContent = stats.exams_taken;
    document.getElementById('profile-strong-topic').textContent = stats.strongest_topic || 'Not yet assessed';
    document.getElementById('profile-weak-topic').textContent = stats.weakest_topic || 'Not yet assessed';
  } catch (e) {
    console.error("Failed to load profile stats", e);
  }
}

// --- Analytics Subview ---
async function loadAnalytics() {
  try {
    // 1. Topic heatmap stats
    const allTopics = await apiRequest('/api/topics');
    const topicStats = await apiRequest('/api/student/topic-stats');
    
    const statsMap = {};
    topicStats.forEach(ts => {
      statsMap[ts.topic_name] = ts;
    });
    
    const grid = document.getElementById('analytics-heatmap-grid');
    grid.innerHTML = '';
    
    allTopics.forEach(t => {
      const stat = statsMap[t.topic_name];
      let accuracyText = 'Unseen';
      let cssClass = 'level-unseen';
      
      if (stat) {
        const accuracy = stat.accuracy;
        accuracyText = `${accuracy.toFixed(0)}%`;
        if (accuracy < 20) cssClass = 'level-0-20';
        else if (accuracy < 40) cssClass = 'level-20-40';
        else if (accuracy < 60) cssClass = 'level-40-60';
        else if (accuracy < 80) cssClass = 'level-60-80';
        else cssClass = 'level-80-100';
      }
      
      const cell = document.createElement('div');
      cell.className = `heatmap-cell ${cssClass}`;
      cell.onclick = () => startHeatmapTopicPractice(t.topic_name);
      cell.innerHTML = `
        <span class="cell-name" title="${t.topic_name}">${t.topic_name}</span>
        <span class="cell-acc">${accuracyText}</span>
      `;
      grid.appendChild(cell);
    });
    
    // 2. Confidence Matrix Counts
    try {
      const confProfile = await apiRequest('/api/student/confidence-profile');
      document.getElementById('matrix-confident').textContent = confProfile.confident_master || 0;
      document.getElementById('matrix-lucky-guess').textContent = confProfile.lucky_guess || 0;
      document.getElementById('matrix-confident-wrong').textContent = confProfile.confident_mistake || 0;
      document.getElementById('matrix-no-knowledge').textContent = confProfile.no_knowledge || 0;
    } catch (err) {
      console.error("Failed to load confidence profile", err);
      document.getElementById('matrix-confident').textContent = 0;
      document.getElementById('matrix-lucky-guess').textContent = 0;
      document.getElementById('matrix-confident-wrong').textContent = 0;
      document.getElementById('matrix-no-knowledge').textContent = 0;
    }
    
    // 3. Fetch Semantic Confusion Clusters
    try {
      const confusionList = document.getElementById('confusion-clusters-list');
      if (confusionList) {
        const clusters = await apiRequest('/api/student/confusion-clusters');
        if (clusters && clusters.length > 0) {
          confusionList.innerHTML = '';
          clusters.forEach(c => {
            const item = document.createElement('div');
            item.className = 'confusion-cluster-item';
            
            const srcQ = c.source_mcq;
            const tgtQ = c.confused_with;
            
            item.innerHTML = `
              <div class="confusion-cluster-header">
                <span class="confusion-cluster-title">Concept Confusion Spotted</span>
                <span class="confusion-cluster-tag">${c.wrong_count} Wrong Answers</span>
              </div>
              <div class="confusion-cluster-body">
                <div class="vs-col">
                  <span class="vs-col-label">Topic: ${c.topic}</span>
                  <span class="vs-col-text">${srcQ.question}</span>
                  <div style="margin-top: 8px; font-size: 13px; font-weight: 600; color: var(--success);">
                    Correct Answer: Option ${srcQ.correct}
                  </div>
                </div>
                <div class="vs-col">
                  <span class="vs-col-label">Confused With: ${tgtQ.topic}</span>
                  <span class="vs-col-text">${tgtQ.question}</span>
                  <div style="margin-top: 8px; font-size: 13px; font-weight: 600; color: var(--primary);">
                    Correct Answer: Option ${tgtQ.correct}
                  </div>
                </div>
              </div>
              <div class="confusion-cluster-explanation">
                <strong>Diagnosis:</strong> ${c.explanation}
              </div>
            `;
            confusionList.appendChild(item);
          });
        } else {
          confusionList.innerHTML = `<div class="empty-state-message">No confusion clusters detected yet. Keep practicing to build your diagnostic map!</div>`;
        }
      }
    } catch (err) {
      console.error("Failed to load confusion clusters", err);
    }
    
    // 4. Render Learning Curve Line Chart
    renderLearningCurveChart();
    
  } catch (e) {
    console.error("Analytics load failed", e);
  }
}

function startHeatmapTopicPractice(topicName) {
  if (confirm(`Do you want to start a 10-question practice session on "${topicName}"?`)) {
    setTimeout(async () => {
      const payload = {
        mode: 'adaptive',
        count: 10,
        topic_name: topicName,
        chapter_name: null
      };
      
      try {
        showPracticePreparing();
        const sessionData = await apiRequest('/api/practice/session', 'POST', payload);
        await startPracticeExamFromSession(sessionData, 'adaptive', 10);
      } catch (e) {
        alert("Failed to start session: " + e.message);
      } finally {
        hidePracticePreparing();
      }
    }, 0);
  }
}

async function renderLearningCurveChart() {
  const canvas = document.getElementById('learning-curve-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  
  if (state.learningCurveChart) {
    state.learningCurveChart.destroy();
  }
  
  let labels = [];
  let dataset = [];
  
  try {
    const timeline = await apiRequest('/api/student/timeline');
    if (timeline && timeline.length > 0) {
      timeline.forEach(item => {
        const dateObj = new Date(item.date);
        const formattedDate = dateObj.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
        labels.push(formattedDate);
        dataset.push(Math.round(item.accuracy));
      });
    } else {
      labels = ['Assessment Start'];
      dataset = [0];
    }
  } catch (err) {
    console.error("Failed to load timeline for chart", err);
    labels = ['Error'];
    dataset = [0];
  }
  
  state.learningCurveChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels: labels,
      datasets: [{
        label: 'Accuracy Progress (%)',
        data: dataset,
        borderColor: '#2B4C3F',
        backgroundColor: 'rgba(43, 76, 63, 0.08)',
        borderWidth: 2,
        tension: 0.3,
        fill: true
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: {
          display: false
        }
      },
      scales: {
        x: {
          grid: {
            color: 'rgba(0, 0, 0, 0.03)'
          },
          ticks: {
            color: '#78716C'
          }
        },
        y: {
          min: 0,
          max: 100,
          grid: {
            color: 'rgba(0, 0, 0, 0.03)'
          },
          ticks: {
            color: '#78716C'
          }
        }
      }
    }
  });
}
