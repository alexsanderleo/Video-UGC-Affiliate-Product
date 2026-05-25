/**
 * Video Affiliate AI Generator — Frontend Logic
 * Handles drag-drop, validation, API calls, progress tracking, and output display.
 */

(function () {
    'use strict';

    // === DOM References ===
    const dropZone = document.getElementById('dropZone');
    const dropZoneContent = document.getElementById('dropZoneContent');
    const dropZonePreview = document.getElementById('dropZonePreview');
    const previewVideo = document.getElementById('previewVideo');
    const previewInfo = document.getElementById('previewInfo');
    const fileInput = document.getElementById('fileInput');
    const btnRemoveVideo = document.getElementById('btnRemoveVideo');

    const toggleText = document.getElementById('toggleText');
    const toggleLogo = document.getElementById('toggleLogo');
    const watermarkTextGroup = document.getElementById('watermarkTextGroup');
    const watermarkLogoGroup = document.getElementById('watermarkLogoGroup');
    const watermarkText = document.getElementById('watermarkText');
    const logoInput = document.getElementById('logoInput');
    const logoUploadZone = document.getElementById('logoUploadZone');
    const logoUploadContent = document.getElementById('logoUploadContent');
    const logoPreview = document.getElementById('logoPreview');
    const logoPreviewImg = document.getElementById('logoPreviewImg');
    const btnRemoveLogo = document.getElementById('btnRemoveLogo');

    const voiceSelect = document.getElementById('voiceSelect');
    const voiceCardFemale = document.getElementById('voiceCardFemale');
    const voiceCardMale = document.getElementById('voiceCardMale');

    const btnGenerate = document.getElementById('btnGenerate');
    const btnContent = document.getElementById('btnContent');
    const btnLoading = document.getElementById('btnLoading');
    const btnLoadingText = document.getElementById('btnLoadingText');

    const progressPanel = document.getElementById('progressPanel');
    const progressBar = document.getElementById('progressBar');
    const stepA = document.getElementById('stepA');
    const stepB = document.getElementById('stepB');
    const stepC = document.getElementById('stepC');
    const stepAStatus = document.getElementById('stepAStatus');
    const stepBStatus = document.getElementById('stepBStatus');
    const stepCStatus = document.getElementById('stepCStatus');

    const outputSection = document.getElementById('outputSection');
    const outputVideo = document.getElementById('outputVideo');
    const outputCaption = document.getElementById('outputCaption');
    const btnDownload = document.getElementById('btnDownload');
    const btnCopy = document.getElementById('btnCopy');
    const copyBtnText = document.getElementById('copyBtnText');

    // === State ===
    let selectedVideoFile = null;
    let selectedLogoFile = null;
    let watermarkMode = 'text'; // 'text' or 'logo'
    let isProcessing = false;

    // === Constants ===
    const MAX_FILE_SIZE = 100 * 1024 * 1024; // 100 MB
    const MIN_DURATION = 5;   // seconds (lenient for PoC, spec says 30)
    const MAX_DURATION = 120; // seconds (lenient for PoC, spec says 60)

    // === Utility: Show Error Toast ===
    function showToast(message, isError = true) {
        // Remove existing toast
        const existing = document.querySelector('.error-toast');
        if (existing) existing.remove();

        const toast = document.createElement('div');
        toast.className = 'error-toast';
        if (!isError) {
            toast.style.background = 'rgba(16, 185, 129, 0.95)';
            toast.style.boxShadow = '0 8px 32px rgba(16, 185, 129, 0.3)';
        }
        toast.textContent = message;
        document.body.appendChild(toast);

        requestAnimationFrame(() => {
            toast.classList.add('show');
        });

        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 300);
        }, 4000);
    }

    // === Utility: Format file size ===
    function formatSize(bytes) {
        if (bytes < 1024) return bytes + ' B';
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
    }

    // === Utility: Format duration ===
    function formatDuration(seconds) {
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return m > 0 ? `${m}m ${s}s` : `${s}s`;
    }

    // === Video Duration Check ===
    function getVideoDuration(file) {
        return new Promise((resolve, reject) => {
            const video = document.createElement('video');
            video.preload = 'metadata';
            const url = URL.createObjectURL(file);
            video.src = url;
            video.onloadedmetadata = () => {
                URL.revokeObjectURL(url);
                resolve(video.duration);
            };
            video.onerror = () => {
                URL.revokeObjectURL(url);
                reject(new Error('Gagal membaca metadata video'));
            };
        });
    }

    // === Update Generate Button State ===
    function updateGenerateBtn() {
        const token = localStorage.getItem('token');
        if (!token) {
            btnGenerate.disabled = false;
            return;
        }
        const hasVideo = selectedVideoFile !== null;
        const hasWatermark = watermarkMode === 'text'
            ? watermarkText.value.trim().length > 0
            : selectedLogoFile !== null;
        btnGenerate.disabled = !hasVideo || !hasWatermark || isProcessing;
    }

    // === File Validation & Selection ===
    async function handleVideoFile(file) {
        // Type check
        if (!file.type.match(/video\/mp4/) && !file.name.toLowerCase().endsWith('.mp4')) {
            showToast('❌ Hanya file .mp4 yang diterima!');
            return;
        }

        // Size check
        if (file.size > MAX_FILE_SIZE) {
            showToast(`❌ Ukuran file terlalu besar! Maksimal ${formatSize(MAX_FILE_SIZE)}`);
            return;
        }

        // Duration check
        try {
            const duration = await getVideoDuration(file);
            if (duration < MIN_DURATION) {
                showToast(`❌ Durasi video terlalu pendek (${formatDuration(duration)}). Minimal ${MIN_DURATION} detik.`);
                return;
            }
            if (duration > MAX_DURATION) {
                showToast(`❌ Durasi video terlalu panjang (${formatDuration(duration)}). Maksimal ${MAX_DURATION} detik.`);
                return;
            }

            // Accept file
            selectedVideoFile = file;
            showVideoPreview(file, duration);
            updateGenerateBtn();

        } catch (err) {
            showToast('❌ Gagal membaca file video. Pastikan file valid.');
            console.error(err);
        }
    }

    function showVideoPreview(file, duration) {
        const url = URL.createObjectURL(file);
        previewVideo.src = url;
        previewInfo.innerHTML = `
            <span>📁 ${file.name}</span>
            <span>💾 ${formatSize(file.size)}</span>
            <span>⏱️ ${formatDuration(duration)}</span>
        `;

        dropZoneContent.style.display = 'none';
        dropZonePreview.style.display = 'flex';
        dropZone.classList.add('has-file');
    }

    function removeVideo() {
        selectedVideoFile = null;
        previewVideo.src = '';
        dropZoneContent.style.display = 'flex';
        dropZonePreview.style.display = 'none';
        dropZone.classList.remove('has-file');
        fileInput.value = '';
        updateGenerateBtn();
    }

    // === Drag & Drop Handlers ===
    dropZone.addEventListener('click', (e) => {
        if (e.target.closest('.btn-remove')) return;
        fileInput.click();
    });

    fileInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            handleVideoFile(e.target.files[0]);
        }
    });

    dropZone.addEventListener('dragover', (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.add('drag-over');
    });

    dropZone.addEventListener('dragleave', (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.remove('drag-over');
    });

    dropZone.addEventListener('drop', (e) => {
        e.preventDefault();
        e.stopPropagation();
        dropZone.classList.remove('drag-over');
        if (e.dataTransfer.files.length > 0) {
            handleVideoFile(e.dataTransfer.files[0]);
        }
    });

    btnRemoveVideo.addEventListener('click', (e) => {
        e.stopPropagation();
        removeVideo();
    });

    // === Watermark Toggle ===
    toggleText.addEventListener('click', () => {
        watermarkMode = 'text';
        toggleText.classList.add('active');
        toggleLogo.classList.remove('active');
        watermarkTextGroup.style.display = 'block';
        watermarkLogoGroup.style.display = 'none';
        updateGenerateBtn();
    });

    toggleLogo.addEventListener('click', () => {
        watermarkMode = 'logo';
        toggleLogo.classList.add('active');
        toggleText.classList.remove('active');
        watermarkTextGroup.style.display = 'none';
        watermarkLogoGroup.style.display = 'block';
        updateGenerateBtn();
    });

    watermarkText.addEventListener('input', updateGenerateBtn);

    // === Logo Upload ===
    logoUploadZone.addEventListener('click', () => logoInput.click());

    logoInput.addEventListener('change', (e) => {
        if (e.target.files.length > 0) {
            const file = e.target.files[0];
            if (!file.type.match(/image\/png/)) {
                showToast('❌ Hanya file .png yang diterima untuk logo!');
                return;
            }
            selectedLogoFile = file;
            const url = URL.createObjectURL(file);
            logoPreviewImg.src = url;
            logoUploadContent.style.display = 'none';
            logoPreview.style.display = 'inline-flex';
            updateGenerateBtn();
        }
    });

    btnRemoveLogo.addEventListener('click', (e) => {
        e.stopPropagation();
        selectedLogoFile = null;
        logoPreviewImg.src = '';
        logoInput.value = '';
        logoUploadContent.style.display = 'flex';
        logoPreview.style.display = 'none';
        updateGenerateBtn();
    });

    // === Voice Selector ===
    voiceSelect.addEventListener('change', () => {
        const val = voiceSelect.value;
        if (val === 'id-ID-GadisNeural') {
            voiceCardFemale.classList.add('active');
            voiceCardMale.classList.remove('active');
        } else {
            voiceCardMale.classList.add('active');
            voiceCardFemale.classList.remove('active');
        }
    });

    voiceCardFemale.addEventListener('click', () => {
        voiceSelect.value = 'id-ID-GadisNeural';
        voiceSelect.dispatchEvent(new Event('change'));
    });

    voiceCardMale.addEventListener('click', () => {
        voiceSelect.value = 'id-ID-ArdiNeural';
        voiceSelect.dispatchEvent(new Event('change'));
    });

    // === Progress Tracking ===
    function resetProgress() {
        [stepA, stepB, stepC].forEach(s => {
            s.className = 'progress-step';
        });
        stepAStatus.textContent = 'Menunggu...';
        stepBStatus.textContent = 'Menunggu...';
        stepCStatus.textContent = 'Menunggu...';
        progressBar.style.width = '0%';
    }

    function setStepState(stepEl, statusEl, state, text) {
        stepEl.className = 'progress-step ' + state; // '' | 'active' | 'done' | 'error'
        statusEl.textContent = text;
    }

    // === Generate Pipeline ===
    btnGenerate.addEventListener('click', async () => {
        // Force Login Check FIRST
        const token = localStorage.getItem('token');
        if (!token) {
            authOverlay.style.display = 'flex';
            loginForm.style.display = 'block';
            registerForm.style.display = 'none';
            authTitle.textContent = 'Login dahulu';
            showToast('⚠️ Silakan login terlebih dahulu untuk melakukan generate!');
            return;
        }

        if (isProcessing) return;

        if (!selectedVideoFile) {
            showToast('⚠️ Silakan upload video produk terlebih dahulu!');
            return;
        }

        const hasWatermark = watermarkMode === 'text'
            ? watermarkText.value.trim().length > 0
            : selectedLogoFile !== null;

        if (!hasWatermark) {
            showToast(watermarkMode === 'text' ? '⚠️ Silakan isi teks watermark!' : '⚠️ Silakan upload logo watermark PNG!');
            return;
        }

        isProcessing = true;
        updateGenerateBtn();


        // Show loading state
        btnContent.style.display = 'none';
        btnLoading.style.display = 'flex';
        btnGenerate.classList.add('processing');
        btnLoadingText.textContent = 'Memproses...';

        // Show progress panel, hide output
        progressPanel.style.display = 'block';
        outputSection.style.display = 'none';
        resetProgress();

        // Build FormData
        const formData = new FormData();
        formData.append('video', selectedVideoFile);
        formData.append('voice', voiceSelect.value);
        formData.append('watermark_mode', watermarkMode);

        if (watermarkMode === 'text') {
            formData.append('watermark_text', watermarkText.value.trim());
            const posSelect = document.getElementById('watermarkPosition');
            formData.append('watermark_position', posSelect ? posSelect.value : 'top-right');
        } else if (selectedLogoFile) {
            formData.append('watermark_logo', selectedLogoFile);
        }

        try {
            // Start SSE-based progress tracking
            const taskId = Date.now().toString();
            formData.append('task_id', taskId);

            // Use EventSource for progress (start before the POST)
            let progressSource = null;

            // Step A: Active
            setStepState(stepA, stepAStatus, 'active', 'Menganalisis video dengan AI...');
            progressBar.style.width = '10%';
            btnLoadingText.textContent = 'AI menganalisis video...';

            // Retrieve JWT token
            const token = localStorage.getItem('token');
            const headers = {};
            if (token) {
                headers['Authorization'] = `Bearer ${token}`;
            }

            // Send to backend
            const response = await fetch('/api/generate', {
                method: 'POST',
                body: formData,
                headers: headers
            });

            if (!response.ok) {
                const errData = await response.json().catch(() => ({ error: 'Server error' }));
                throw new Error(errData.error || `HTTP ${response.status}`);
            }

            // Read SSE-style streamed response
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let finalResult = null;

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop(); // Keep incomplete line in buffer

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.slice(6));
                            handleProgressUpdate(data);
                            if (data.status === 'complete') {
                                finalResult = data;
                            }
                        } catch (e) {
                            // Ignore parse errors for incomplete chunks
                        }
                    }
                }
            }

            // Process any remaining buffer
            if (buffer.startsWith('data: ')) {
                try {
                    const data = JSON.parse(buffer.slice(6));
                    handleProgressUpdate(data);
                    if (data.status === 'complete') {
                        finalResult = data;
                    }
                } catch (e) { }
            }

            if (finalResult) {
                showOutput(finalResult);
            } else {
                throw new Error('Pipeline selesai tanpa hasil');
            }

        } catch (err) {
            console.error('Generate error:', err);
            showToast('❌ ' + err.message);

            // Mark current active step as error
            const activeStep = document.querySelector('.progress-step.active');
            if (activeStep) {
                const statusEl = activeStep.querySelector('.step-status');
                activeStep.className = 'progress-step error';
                if (statusEl) statusEl.textContent = 'Gagal: ' + err.message;
            }
        } finally {
            isProcessing = false;
            btnContent.style.display = 'flex';
            btnLoading.style.display = 'none';
            btnGenerate.classList.remove('processing');
            updateGenerateBtn();
        }
    });

    function handleProgressUpdate(data) {
        switch (data.step) {
            case 'A_start':
                setStepState(stepA, stepAStatus, 'active', 'Menganalisis video dengan Qwen AI...');
                progressBar.style.width = '10%';
                btnLoadingText.textContent = 'AI menganalisis video...';
                break;
            case 'A_done':
                setStepState(stepA, stepAStatus, 'done', '✓ Skrip narasi berhasil dibuat');
                progressBar.style.width = '35%';
                break;
            case 'B_start':
                setStepState(stepB, stepBStatus, 'active', 'Mengkonversi teks ke suara AI...');
                progressBar.style.width = '40%';
                btnLoadingText.textContent = 'Generating suara AI...';
                break;
            case 'B_done':
                setStepState(stepB, stepBStatus, 'done', '✓ Audio narasi berhasil dibuat');
                progressBar.style.width = '60%';
                break;
            case 'C_start':
                setStepState(stepC, stepCStatus, 'active', 'Merender video final dengan FFmpeg...');
                progressBar.style.width = '65%';
                btnLoadingText.textContent = 'FFmpeg rendering video...';
                break;
            case 'C_progress':
                if (data.percent) {
                    const pct = 65 + (data.percent * 0.3); // 65-95%
                    progressBar.style.width = pct + '%';
                    setStepState(stepC, stepCStatus, 'active', `Rendering... ${Math.round(data.percent)}%`);
                }
                break;
            case 'C_done':
                setStepState(stepC, stepCStatus, 'done', '✓ Video final berhasil dirender');
                progressBar.style.width = '100%';
                btnLoadingText.textContent = 'Selesai!';
                break;
            case 'error':
                showToast('❌ ' + (data.message || 'Terjadi kesalahan'));
                break;
        }
    }

    function showOutput(result) {
        // Show output section
        outputSection.style.display = 'block';

        // Set video source
        outputVideo.src = result.video_url;
        outputVideo.load();

        // Set download link
        btnDownload.href = result.video_url;
        btnDownload.download = result.filename || 'video_affiliate_ai.mp4';

        // Set caption
        outputCaption.value = result.caption || '';

        // Scroll to output
        setTimeout(() => {
            outputSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 300);

        showToast('✅ Video berhasil di-generate!', false);
    }

    // === Copy Caption ===
    btnCopy.addEventListener('click', async () => {
        try {
            await navigator.clipboard.writeText(outputCaption.value);
            copyBtnText.textContent = '✓ Tersalin!';
            btnCopy.classList.add('copied');
            setTimeout(() => {
                copyBtnText.textContent = 'Copy Caption';
                btnCopy.classList.remove('copied');
            }, 2000);
        } catch (err) {
            // Fallback
            outputCaption.select();
            document.execCommand('copy');
            copyBtnText.textContent = '✓ Tersalin!';
            setTimeout(() => {
                copyBtnText.textContent = 'Copy Caption';
            }, 2000);
        }
    });

    // === Authentication Logic ===
    const authOverlay = document.getElementById('authOverlay');
    const loginForm = document.getElementById('loginForm');
    const registerForm = document.getElementById('registerForm');
    const loginEmail = document.getElementById('loginEmail');
    const loginPassword = document.getElementById('loginPassword');
    const registerName = document.getElementById('registerName');
    const registerEmail = document.getElementById('registerEmail');
    const registerPassword = document.getElementById('registerPassword');
    const switchToRegister = document.getElementById('switchToRegister');
    const switchToLogin = document.getElementById('switchToLogin');
    const authTitle = document.getElementById('authTitle');

    const userProfile = document.getElementById('userProfile');
    const userEmailBadge = document.getElementById('userEmail');
    const btnLogout = document.getElementById('btnLogout');

    // Switch forms
    switchToRegister.addEventListener('click', (e) => {
        e.preventDefault();
        loginForm.style.display = 'none';
        registerForm.style.display = 'block';
        authTitle.textContent = 'Daftar Akun Baru';
    });

    switchToLogin.addEventListener('click', (e) => {
        e.preventDefault();
        registerForm.style.display = 'none';
        loginForm.style.display = 'block';
        authTitle.textContent = 'Login dahulu';
    });

    // Handle Login
    loginForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const email = loginEmail.value.trim();
        const password = loginPassword.value;

        try {
            const res = await fetch('/api/v1/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password })
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: 'Login gagal' }));
                throw new Error(err.detail || 'Email atau password salah');
            }

            const data = await res.json();
            localStorage.setItem('token', data.access_token);
            localStorage.setItem('email', email);
            showToast('✅ Berhasil masuk!', false);
            initAuth();
        } catch (err) {
            showToast('❌ ' + err.message);
        }
    });

    // Handle Register
    registerForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        const full_name = registerName.value.trim();
        const email = registerEmail.value.trim();
        const password = registerPassword.value;
        const price_plan = document.getElementById('registerPricePlan').value;

        try {
            const res = await fetch('/api/v1/auth/register', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password, full_name, price_plan })
            });

            if (!res.ok) {
                const err = await res.json().catch(() => ({ detail: 'Registrasi gagal' }));
                throw new Error(err.detail || 'Email sudah terdaftar');
            }

            showToast('✅ Pendaftaran sukses! Silakan login.', false);
            // Switch back to login
            registerForm.style.display = 'none';
            loginForm.style.display = 'block';
            authTitle.textContent = 'Login Akun dahulu';
            loginEmail.value = email;
            loginPassword.value = '';
        } catch (err) {
            showToast('❌ ' + err.message);
        }
    });

    // Handle Logout
    btnLogout.addEventListener('click', () => {
        localStorage.removeItem('token');
        localStorage.removeItem('email');
        showToast('🚪 Keluar dari sesi...', false);
        initAuth();
    });

    // Initialize Auth state
    async function initAuth() {
        const token = localStorage.getItem('token');
        const guestProfile = document.getElementById('guestProfile');
        const appSidebar = document.getElementById('appSidebar');

        if (appSidebar) appSidebar.style.display = 'flex';

        if (!token) {
            authOverlay.style.display = 'none'; // Keep hidden on load
            userProfile.style.display = 'none';
            if (guestProfile) guestProfile.style.display = 'flex';
            updateGenerateBtn();
            updateConvertBtn();
            return;
        }

        // Verify token is still valid with /auth/me
        try {
            const res = await fetch('/api/v1/auth/me', {
                headers: { 'Authorization': `Bearer ${token}` }
            });

            if (!res.ok) {
                throw new Error('Sesi kedaluwarsa');
            }

            const userData = await res.json();

            // Success: hide auth and show profile
            authOverlay.style.display = 'none';
            userProfile.style.display = 'flex';
            userEmailBadge.textContent = userData.email;
            if (guestProfile) guestProfile.style.display = 'none';
        } catch (err) {
            localStorage.removeItem('token');
            localStorage.removeItem('email');
            authOverlay.style.display = 'none'; // Keep hidden
            userProfile.style.display = 'none';
            if (guestProfile) guestProfile.style.display = 'flex';
        } finally {
            updateGenerateBtn();
            updateConvertBtn();
        }
    }

    // Sidebar Guest Login Button
    const btnSidebarLogin = document.getElementById('btnSidebarLogin');
    if (btnSidebarLogin) {
        btnSidebarLogin.addEventListener('click', () => {
            authOverlay.style.display = 'flex';
            loginForm.style.display = 'block';
            registerForm.style.display = 'none';
            authTitle.textContent = 'Login dahulu';
        });
    }

    // Close Auth Modal via Close Button
    const btnCloseAuth = document.getElementById('btnCloseAuth');
    if (btnCloseAuth) {
        btnCloseAuth.addEventListener('click', () => {
            authOverlay.style.display = 'none';
        });
    }

    // Close Auth Modal via clicking outside the card
    if (authOverlay) {
        authOverlay.addEventListener('click', (e) => {
            if (e.target === authOverlay) {
                authOverlay.style.display = 'none';
            }
        });
    }


    // === Multi-Page / Tab Navigation ===
    const menuBtnGenerator = document.getElementById('menuBtnGenerator');
    const menuBtnBulkGenerator = document.getElementById('menuBtnBulkGenerator');
    const menuBtnConverter = document.getElementById('menuBtnConverter');
    const videoGeneratorPage = document.getElementById('videoGeneratorPage');
    const bulkGeneratorPage = document.getElementById('bulkGeneratorPage');
    const convertVideoPage = document.getElementById('convertVideoPage');

    function switchTab(activeBtn, activePage) {
        [menuBtnGenerator, menuBtnBulkGenerator, menuBtnConverter].forEach(btn => {
            if (btn) btn.classList.remove('active');
        });
        [videoGeneratorPage, bulkGeneratorPage, convertVideoPage].forEach(page => {
            if (page) {
                page.classList.remove('active');
                page.style.display = 'none';
            }
        });

        if (activeBtn) activeBtn.classList.add('active');
        if (activePage) {
            activePage.classList.add('active');
            activePage.style.display = 'block';
        }
    }

    if (menuBtnGenerator) {
        menuBtnGenerator.addEventListener('click', () => switchTab(menuBtnGenerator, videoGeneratorPage));
    }
    if (menuBtnBulkGenerator) {
        menuBtnBulkGenerator.addEventListener('click', () => switchTab(menuBtnBulkGenerator, bulkGeneratorPage));
    }
    if (menuBtnConverter) {
        menuBtnConverter.addEventListener('click', () => switchTab(menuBtnConverter, convertVideoPage));
    }
    }

    // === Convert Video Size Logic ===
    const convertDropZone = document.getElementById('convertDropZone');
    const convertDropZoneContent = document.getElementById('convertDropZoneContent');
    const convertDropZonePreview = document.getElementById('convertDropZonePreview');
    const convertPreviewVideo = document.getElementById('convertPreviewVideo');
    const convertPreviewInfo = document.getElementById('convertPreviewInfo');
    const convertFileInput = document.getElementById('convertFileInput');
    const btnRemoveConvertVideo = document.getElementById('btnRemoveConvertVideo');
    const compressionSelect = document.getElementById('compressionSelect');
    const btnConvert = document.getElementById('btnConvert');
    const btnConvertContent = document.getElementById('btnConvertContent');
    const btnConvertLoading = document.getElementById('btnConvertLoading');
    const btnConvertLoadingText = document.getElementById('btnConvertLoadingText');
    const convertProgressPanel = document.getElementById('convertProgressPanel');
    const convertProgressBar = document.getElementById('convertProgressBar');
    const convertStepC = document.getElementById('convertStepC');
    const convertStepCStatus = document.getElementById('convertStepCStatus');
    const convertOutputSection = document.getElementById('convertOutputSection');
    const convertOutputVideo = document.getElementById('convertOutputVideo');
    const btnConvertDownload = document.getElementById('btnConvertDownload');
    const comparisonSavingPct = document.getElementById('comparisonSavingPct');
    const comparisonOriginalSize = document.getElementById('comparisonOriginalSize');
    const comparisonCompressedSize = document.getElementById('comparisonCompressedSize');

    let selectedConvertVideoFile = null;
    let isConverting = false;

    function updateConvertBtn() {
        const token = localStorage.getItem('token');
        if (!token) {
            btnConvert.disabled = false;
            return;
        }
        const hasVideo = selectedConvertVideoFile !== null;
        btnConvert.disabled = !hasVideo || isConverting;
    }

    async function handleConvertVideoFile(file) {
        if (!file.type.startsWith('video/') && !file.name.toLowerCase().match(/\.(mp4|mkv|avi|mov|webm)$/)) {
            showToast('❌ Hanya file video yang diterima!');
            return;
        }

        if (file.size > MAX_FILE_SIZE) {
            showToast(`❌ Ukuran file terlalu besar! Maksimal ${formatSize(MAX_FILE_SIZE)}`);
            return;
        }

        try {
            const duration = await getVideoDuration(file);
            selectedConvertVideoFile = file;
            showConvertVideoPreview(file, duration);
            updateConvertBtn();
        } catch (err) {
            selectedConvertVideoFile = file;
            showConvertVideoPreview(file, 0);
            updateConvertBtn();
        }
    }

    function showConvertVideoPreview(file, duration) {
        const url = URL.createObjectURL(file);
        convertPreviewVideo.src = url;
        convertPreviewInfo.innerHTML = `
            <span>📁 ${file.name}</span>
            <span>💾 ${formatSize(file.size)}</span>
            ${duration > 0 ? `<span>⏱️ ${formatDuration(duration)}</span>` : ''}
        `;

        convertDropZoneContent.style.display = 'none';
        convertDropZonePreview.style.display = 'flex';
        convertDropZone.classList.add('has-file');
    }

    function removeConvertVideo() {
        selectedConvertVideoFile = null;
        convertPreviewVideo.src = '';
        convertDropZoneContent.style.display = 'flex';
        convertDropZonePreview.style.display = 'none';
        convertDropZone.classList.remove('has-file');
        convertFileInput.value = '';
        updateConvertBtn();
    }

    // Event Listeners for Conversion
    if (convertDropZone) {
        convertDropZone.addEventListener('click', (e) => {
            if (e.target.closest('.btn-remove')) return;
            convertFileInput.click();
        });

        convertFileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                handleConvertVideoFile(e.target.files[0]);
            }
        });

        convertDropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            convertDropZone.classList.add('drag-over');
        });

        convertDropZone.addEventListener('dragleave', (e) => {
            e.preventDefault();
            e.stopPropagation();
            convertDropZone.classList.remove('drag-over');
        });

        convertDropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            convertDropZone.classList.remove('drag-over');
            if (e.dataTransfer.files.length > 0) {
                handleConvertVideoFile(e.dataTransfer.files[0]);
            }
        });
    }

    if (btnRemoveConvertVideo) {
        btnRemoveConvertVideo.addEventListener('click', (e) => {
            e.stopPropagation();
            removeConvertVideo();
        });
    }

    if (btnConvert) {
        btnConvert.addEventListener('click', async () => {
            // Force Login Check FIRST
            const token = localStorage.getItem('token');
            if (!token) {
                authOverlay.style.display = 'flex';
                loginForm.style.display = 'block';
                registerForm.style.display = 'none';
                authTitle.textContent = 'Login dahulu';
                showToast('⚠️ Silakan login terlebih dahulu untuk melakukan konversi!');
                return;
            }

            if (isConverting) return;

            if (!selectedConvertVideoFile) {
                showToast('⚠️ Silakan upload video terlebih dahulu!');
                return;
            }

            isConverting = true;
            updateConvertBtn();


            btnConvertContent.style.display = 'none';
            btnConvertLoading.style.display = 'flex';
            btnConvert.classList.add('processing');
            btnConvertLoadingText.textContent = 'Memulai...';

            convertProgressPanel.style.display = 'block';
            convertOutputSection.style.display = 'none';
            convertStepC.className = 'progress-step';
            convertStepCStatus.textContent = 'Menunggu...';
            convertProgressBar.style.width = '0%';

            const formData = new FormData();
            formData.append('video', selectedConvertVideoFile);
            formData.append('crf_level', compressionSelect.value);

            try {
                setStepState(convertStepC, convertStepCStatus, 'active', 'Mengupload video ke server...');
                convertProgressBar.style.width = '5%';
                btnConvertLoadingText.textContent = 'Uploading...';

                const token = localStorage.getItem('token');
                const headers = {};
                if (token) {
                    headers['Authorization'] = `Bearer ${token}`;
                }

                const response = await fetch('/api/v1/convert', {
                    method: 'POST',
                    body: formData,
                    headers: headers
                });

                if (!response.ok) {
                    const errData = await response.json().catch(() => ({ detail: 'Server error' }));
                    throw new Error(errData.detail || `HTTP ${response.status}`);
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';
                let finalResult = null;

                setStepState(convertStepC, convertStepCStatus, 'active', 'Memulai konversi di server...');
                convertProgressBar.style.width = '15%';
                btnConvertLoadingText.textContent = 'Processing...';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop();

                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            try {
                                const data = JSON.parse(line.slice(6));
                                handleConvertProgressUpdate(data);
                                if (data.status === 'complete') {
                                    finalResult = data;
                                }
                            } catch (e) { }
                        }
                    }
                }

                if (buffer.startsWith('data: ')) {
                    try {
                        const data = JSON.parse(buffer.slice(6));
                        handleConvertProgressUpdate(data);
                        if (data.status === 'complete') {
                            finalResult = data;
                        }
                    } catch (e) { }
                }

                if (finalResult) {
                    showConvertOutput(finalResult);
                } else {
                    throw new Error('Konversi selesai tanpa hasil');
                }

            } catch (err) {
                console.error('Convert error:', err);
                showToast('❌ ' + err.message);
                setStepState(convertStepC, convertStepCStatus, 'error', 'Gagal: ' + err.message);
            } finally {
                isConverting = false;
                btnConvertContent.style.display = 'flex';
                btnConvertLoading.style.display = 'none';
                btnConvert.classList.remove('processing');
                updateConvertBtn();
            }
        });
    }

    function handleConvertProgressUpdate(data) {
        switch (data.step) {
            case 'C_start':
                setStepState(convertStepC, convertStepCStatus, 'active', 'Mengkompresi video dengan FFmpeg...');
                convertProgressBar.style.width = '20%';
                btnConvertLoadingText.textContent = 'FFmpeg rendering...';
                break;
            case 'C_progress':
                if (data.percent) {
                    const pct = 20 + (data.percent * 0.8); // 20-100%
                    convertProgressBar.style.width = pct + '%';
                    setStepState(convertStepC, convertStepCStatus, 'active', `Mengkompresi... ${Math.round(data.percent)}%`);
                }
                break;
            case 'C_done':
                setStepState(convertStepC, convertStepCStatus, 'done', '✓ Video berhasil dikompresi');
                convertProgressBar.style.width = '100%';
                btnConvertLoadingText.textContent = 'Selesai!';
                break;
            case 'error':
                showToast('❌ ' + (data.message || 'Terjadi kesalahan'));
                break;
        }
    }

    function showConvertOutput(result) {
        convertOutputSection.style.display = 'block';
        convertOutputVideo.src = result.video_url;
        convertOutputVideo.load();

        btnConvertDownload.href = result.video_url;
        btnConvertDownload.download = result.filename || 'video_compressed.mp4';

        comparisonSavingPct.textContent = `${result.saving_percent}%`;
        comparisonOriginalSize.textContent = formatSize(result.original_size);
        comparisonCompressedSize.textContent = formatSize(result.compressed_size);

        setTimeout(() => {
            convertOutputSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }, 300);

        showToast('✅ Video berhasil dikompresi!', false);
    }

    // === Generate Video Massal Logic ===
    const bulkDropZone = document.getElementById('bulkDropZone');
    const bulkDropZoneContent = document.getElementById('bulkDropZoneContent');
    const bulkFileInput = document.getElementById('bulkFileInput');
    const bulkQueueList = document.getElementById('bulkQueueList');
    const bulkDashboard = document.getElementById('bulkDashboard');

    const bulkStatTotal = document.getElementById('bulkStatTotal');
    const bulkStatQueue = document.getElementById('bulkStatQueue');
    const bulkStatActive = document.getElementById('bulkStatActive');
    const bulkStatDone = document.getElementById('bulkStatDone');
    const bulkStatError = document.getElementById('bulkStatError');

    const btnBulkStart = document.getElementById('btnBulkStart');
    const btnBulkClear = document.getElementById('btnBulkClear');

    const bulkVoiceSelect = document.getElementById('bulkVoiceSelect');
    const bulkWatermarkMode = document.getElementById('bulkWatermarkMode');
    const bulkWatermarkTextGroup = document.getElementById('bulkWatermarkTextGroup');
    const bulkWatermarkLogoGroup = document.getElementById('bulkWatermarkLogoGroup');
    const bulkWatermarkText = document.getElementById('bulkWatermarkText');
    const bulkWatermarkPosition = document.getElementById('bulkWatermarkPosition');

    const bulkLogoInput = document.getElementById('bulkLogoInput');
    const bulkLogoUploadZone = document.getElementById('bulkLogoUploadZone');
    const bulkLogoUploadContent = document.getElementById('bulkLogoUploadContent');
    const bulkLogoPreview = document.getElementById('bulkLogoPreview');
    const bulkLogoPreviewImg = document.getElementById('bulkLogoPreviewImg');
    const btnRemoveBulkLogo = document.getElementById('btnRemoveBulkLogo');

    let selectedBulkLogoFile = null;
    let bulkQueue = [];
    const MAX_CONCURRENT = 3;

    // Toggle Watermark Group for Bulk Settings
    if (bulkWatermarkMode) {
        bulkWatermarkMode.addEventListener('change', () => {
            const mode = bulkWatermarkMode.value;
            if (mode === 'text') {
                bulkWatermarkTextGroup.style.display = 'block';
                bulkWatermarkLogoGroup.style.display = 'none';
            } else {
                bulkWatermarkTextGroup.style.display = 'none';
                bulkWatermarkLogoGroup.style.display = 'block';
            }
        });
    }

    // Logo Upload for Bulk Settings
    if (bulkLogoUploadZone) {
        bulkLogoUploadZone.addEventListener('click', () => bulkLogoInput.click());
    }

    if (bulkLogoInput) {
        bulkLogoInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                const file = e.target.files[0];
                if (!file.type.match(/image\/png/)) {
                    showToast('❌ Hanya file .png yang diterima untuk logo!');
                    return;
                }
                selectedBulkLogoFile = file;
                const url = URL.createObjectURL(file);
                bulkLogoPreviewImg.src = url;
                bulkLogoUploadContent.style.display = 'none';
                bulkLogoPreview.style.display = 'inline-flex';
            }
        });
    }

    if (btnRemoveBulkLogo) {
        btnRemoveBulkLogo.addEventListener('click', (e) => {
            e.stopPropagation();
            selectedBulkLogoFile = null;
            bulkLogoPreviewImg.src = '';
            bulkLogoInput.value = '';
            bulkLogoUploadContent.style.display = 'flex';
            bulkLogoPreview.style.display = 'none';
        });
    }

    // Video Selection and Drag-Drop for Bulk Page
    if (bulkDropZone) {
        bulkDropZone.addEventListener('click', (e) => {
            if (e.target.closest('.btn-remove')) return;
            bulkFileInput.click();
        });

        bulkFileInput.addEventListener('change', (e) => {
            if (e.target.files.length > 0) {
                handleBulkVideoFiles(e.target.files);
            }
        });

        bulkDropZone.addEventListener('dragover', (e) => {
            e.preventDefault();
            e.stopPropagation();
            bulkDropZone.classList.add('drag-over');
        });

        bulkDropZone.addEventListener('dragleave', (e) => {
            e.preventDefault();
            e.stopPropagation();
            bulkDropZone.classList.remove('drag-over');
        });

        bulkDropZone.addEventListener('drop', (e) => {
            e.preventDefault();
            e.stopPropagation();
            bulkDropZone.classList.remove('drag-over');
            if (e.dataTransfer.files.length > 0) {
                handleBulkVideoFiles(e.dataTransfer.files);
            }
        });
    }

    // Add Files to Queue
    function handleBulkVideoFiles(files) {
        if (bulkQueue.length + files.length > 20) {
            showToast('❌ Maksimal 20 video dalam satu antrean massal!');
            return;
        }

        let hasAdded = false;
        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            
            // Format check
            if (!file.type.match(/video\/mp4/) && !file.name.toLowerCase().endsWith('.mp4')) {
                showToast(`❌ ${file.name} dilewati: Hanya file .mp4 yang diterima!`);
                continue;
            }

            // Size check
            if (file.size > MAX_FILE_SIZE) {
                showToast(`❌ ${file.name} dilewati: Ukuran melebihi ${formatSize(MAX_FILE_SIZE)}`);
                continue;
            }

            // Create unique job object
            const job = {
                id: 'bulk_' + Math.random().toString(36).substr(2, 9),
                file: file,
                status: 'pending', // 'pending' | 'processing' | 'success' | 'error'
                progress: 0,
                statusText: 'Menunggu Antrean...',
                errorMsg: '',
                outputUrl: '',
                filename: '',
                caption: '',
                reader: null
            };

            bulkQueue.push(job);
            hasAdded = true;
        }

        if (hasAdded) {
            bulkDashboard.style.display = 'block';
            updateBulkStats();
            renderBulkQueue();
            
            // Auto scroll to queue
            setTimeout(() => {
                bulkDashboard.scrollIntoView({ behavior: 'smooth', block: 'start' });
            }, 300);
        }
    }

    // Update Statistics Counter
    function updateBulkStats() {
        const total = bulkQueue.length;
        const queue = bulkQueue.filter(j => j.status === 'pending').length;
        const active = bulkQueue.filter(j => j.status === 'processing').length;
        const done = bulkQueue.filter(j => j.status === 'success').length;
        const error = bulkQueue.filter(j => j.status === 'error').length;

        bulkStatTotal.textContent = total;
        bulkStatQueue.textContent = queue;
        bulkStatActive.textContent = active;
        bulkStatDone.textContent = done;
        bulkStatError.textContent = error;

        // Start button state
        btnBulkStart.disabled = total === 0 || active > 0 || queue === 0;
    }

    // Render Queue Items in Grid
    function renderBulkQueue() {
        bulkQueueList.innerHTML = '';
        if (bulkQueue.length === 0) {
            bulkDashboard.style.display = 'none';
            return;
        }

        bulkQueue.forEach(job => {
            const card = document.createElement('div');
            card.className = `queue-card status-${job.status}`;
            card.id = `card_${job.id}`;

            let outputHtml = '';
            if (job.status === 'success') {
                outputHtml = `
                    <div class="queue-card-output">
                        <div class="queue-card-video-wrapper">
                            <video class="queue-card-video" src="${job.outputUrl}" controls></video>
                        </div>
                        <div class="queue-card-btn-container">
                            <a href="${job.outputUrl}" download="${job.filename || 'video.mp4'}" class="queue-card-btn-download">
                                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                                    <polyline points="7 10 12 15 17 10"/>
                                    <line x1="12" y1="15" x2="12" y2="3"/>
                                </svg>
                                Download
                            </a>
                        </div>
                    </div>
                `;
            } else if (job.status === 'error') {
                outputHtml = `
                    <div style="margin-top: 12px;">
                        <button class="btn-retry" data-id="${job.id}">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/>
                            </svg>
                            Ulangi (Retry)
                        </button>
                    </div>
                `;
            }

            card.innerHTML = `
                <button class="queue-card-remove" data-id="${job.id}">&times;</button>
                <div class="queue-card-header">
                    <span class="queue-card-title" title="${job.file.name}">${job.file.name}</span>
                    <span class="queue-card-size">${formatSize(job.file.size)}</span>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <span class="queue-card-status-badge ${job.status}">${job.status === 'pending' ? 'Antrean' : job.status === 'processing' ? 'Proses' : job.status === 'success' ? 'Sukses' : 'Gagal'}</span>
                </div>
                <div class="queue-card-progress">
                    <div class="queue-card-progress-bar-container">
                        <div class="queue-card-progress-bar" style="width: ${job.progress}%"></div>
                    </div>
                    <span class="queue-card-progress-text" title="${job.statusText}">${job.statusText}</span>
                </div>
                ${outputHtml}
            `;

            // Attach card events
            const btnRemove = card.querySelector('.queue-card-remove');
            if (btnRemove) {
                btnRemove.addEventListener('click', () => removeJobFromQueue(job.id));
            }

            const btnRetry = card.querySelector('.btn-retry');
            if (btnRetry) {
                btnRetry.addEventListener('click', () => retryJob(job.id));
            }

            bulkQueueList.appendChild(card);
        });
    }

    // Remove Item from Queue
    function removeJobFromQueue(id) {
        const index = bulkQueue.findIndex(j => j.id === id);
        if (index !== -1) {
            const job = bulkQueue[index];
            if (job.status === 'processing') {
                showToast('⚠️ Video sedang diproses, tidak bisa dihapus!');
                return;
            }
            bulkQueue.splice(index, 1);
            updateBulkStats();
            renderBulkQueue();
        }
    }

    // Scheduler: concurrent worker execution
    async function processBulkQueue() {
        const activeJobs = bulkQueue.filter(j => j.status === 'processing');
        const pendingJobs = bulkQueue.filter(j => j.status === 'pending');

        if (activeJobs.length >= MAX_CONCURRENT || pendingJobs.length === 0) {
            updateBulkStats();
            return;
        }

        // Trigger up to MAX_CONCURRENT jobs concurrently
        const vacantSlots = MAX_CONCURRENT - activeJobs.length;
        const jobsToStart = pendingJobs.slice(0, vacantSlots);

        jobsToStart.forEach(job => {
            startBulkJob(job);
        });

        updateBulkStats();
    }

    // Start single job pipeline
    async function startBulkJob(job) {
        // Force Login Check per job execution (just in case they logged out mid-run)
        const token = localStorage.getItem('token');
        if (!token) {
            authOverlay.style.display = 'flex';
            loginForm.style.display = 'block';
            registerForm.style.display = 'none';
            authTitle.textContent = 'Login dahulu';
            showToast('⚠️ Silakan login terlebih dahulu untuk melakukan generate massal!');
            
            // Revert job back to pending
            job.status = 'pending';
            job.progress = 0;
            job.statusText = 'Menunggu Antrean...';
            updateBulkStats();
            renderBulkQueue();
            return;
        }

        job.status = 'processing';
        job.progress = 5;
        job.statusText = 'Menghubungkan ke server...';
        updateBulkStats();
        renderBulkQueue();

        const formData = new FormData();
        formData.append('video', job.file);
        formData.append('voice', bulkVoiceSelect.value);
        formData.append('watermark_mode', bulkWatermarkMode.value);

        if (bulkWatermarkMode.value === 'text') {
            formData.append('watermark_text', bulkWatermarkText.value.trim());
            formData.append('watermark_position', bulkWatermarkPosition.value);
        } else if (selectedBulkLogoFile) {
            formData.append('watermark_logo', selectedBulkLogoFile);
        }

        try {
            // Register a task ID
            const taskId = Date.now().toString() + '_' + Math.random().toString(36).substr(2, 5);
            formData.append('task_id', taskId);

            const headers = { 'Authorization': `Bearer ${token}` };

            const response = await fetch('/api/generate', {
                method: 'POST',
                body: formData,
                headers: headers
            });

            if (!response.ok) {
                const errData = await response.json().catch(() => ({ error: 'Gagal memproses video di server' }));
                throw new Error(errData.error || `HTTP ${response.status}`);
            }

            const reader = response.body.getReader();
            job.reader = reader;
            const decoder = new TextDecoder();
            let buffer = '';
            let finalResult = null;

            job.progress = 10;
            job.statusText = 'Menganalisis video dengan AI...';
            updateBulkStats();
            renderBulkQueue();

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;

                buffer += decoder.decode(value, { stream: true });
                const lines = buffer.split('\n');
                buffer = lines.pop();

                for (const line of lines) {
                    if (line.startsWith('data: ')) {
                        try {
                            const data = JSON.parse(line.slice(6));
                            handleJobProgressUpdate(job, data);
                            if (data.status === 'complete') {
                                finalResult = data;
                            }
                        } catch (e) { }
                    }
                }
            }

            if (buffer.startsWith('data: ')) {
                try {
                    const data = JSON.parse(buffer.slice(6));
                    handleJobProgressUpdate(job, data);
                    if (data.status === 'complete') {
                        finalResult = data;
                    }
                } catch (e) { }
            }

            if (finalResult) {
                job.status = 'success';
                job.progress = 100;
                job.statusText = '✓ Selesai';
                job.outputUrl = finalResult.video_url;
                job.filename = finalResult.filename;
                job.caption = finalResult.caption;
            } else {
                throw new Error('Proses selesai tanpa hasil output');
            }

        } catch (err) {
            console.error('Job error:', err);
            job.status = 'error';
            job.progress = 0;
            job.statusText = '❌ Gagal: ' + err.message;
        } finally {
            job.reader = null;
            updateBulkStats();
            renderBulkQueue();
            // Pull the next pending job instantly
            processBulkQueue();
        }
    }

    // Handle single job progress update
    function handleJobProgressUpdate(job, data) {
        switch (data.step) {
            case 'A_start':
                job.progress = 10;
                job.statusText = 'AI menganalisis video...';
                break;
            case 'A_done':
                job.progress = 35;
                job.statusText = '✓ AI selesai menganalisis';
                break;
            case 'B_start':
                job.progress = 40;
                job.statusText = 'Generating suara AI...';
                break;
            case 'B_done':
                job.progress = 60;
                job.statusText = '✓ Suara AI selesai dibuat';
                break;
            case 'C_start':
                job.progress = 65;
                job.statusText = 'Rendering video final...';
                break;
            case 'C_progress':
                if (data.percent) {
                    const pct = 65 + (data.percent * 0.3); // 65-95%
                    job.progress = Math.round(pct);
                    job.statusText = `Rendering... ${Math.round(data.percent)}%`;
                }
                break;
            case 'C_done':
                job.progress = 98;
                job.statusText = 'Menyimpan video...';
                break;
            case 'error':
                job.status = 'error';
                job.statusText = '❌ Gagal: ' + (data.message || 'Terjadi kesalahan');
                break;
        }

        // Live update the card DOM element directly for extreme fast response
        const cardEl = document.getElementById(`card_${job.id}`);
        if (cardEl) {
            const bar = cardEl.querySelector('.queue-card-progress-bar');
            const txt = cardEl.querySelector('.queue-card-progress-text');
            if (bar) bar.style.width = `${job.progress}%`;
            if (txt) {
                txt.textContent = job.statusText;
                txt.title = job.statusText;
            }
        }
    }

    // Retry Failed Job
    function retryJob(id) {
        const job = bulkQueue.find(j => j.id === id);
        if (job && job.status === 'error') {
            const token = localStorage.getItem('token');
            if (!token) {
                authOverlay.style.display = 'flex';
                loginForm.style.display = 'block';
                registerForm.style.display = 'none';
                authTitle.textContent = 'Login dahulu';
                showToast('⚠️ Silakan login terlebih dahulu untuk melakukan retry!');
                return;
            }

            job.status = 'pending';
            job.progress = 0;
            job.statusText = 'Menunggu Antrean...';
            updateBulkStats();
            renderBulkQueue();
            processBulkQueue();
        }
    }

    // Multi-start Trigger
    if (btnBulkStart) {
        btnBulkStart.addEventListener('click', () => {
            const token = localStorage.getItem('token');
            if (!token) {
                authOverlay.style.display = 'flex';
                loginForm.style.display = 'block';
                registerForm.style.display = 'none';
                authTitle.textContent = 'Login dahulu';
                showToast('⚠️ Silakan login terlebih dahulu untuk memulai generate massal!');
                return;
            }

            if (bulkWatermarkMode.value === 'logo' && !selectedBulkLogoFile) {
                showToast('⚠️ Silakan upload logo watermark PNG terlebih dahulu!');
                return;
            }

            showToast('⚡ Memulai pemrosesan massal paralel...', false);
            processBulkQueue();
        });
    }

    // Clear Bulk Queue
    if (btnBulkClear) {
        btnBulkClear.addEventListener('click', () => {
            // Cancel any active readers
            bulkQueue.forEach(job => {
                if (job.reader) {
                    try { job.reader.cancel(); } catch (e) { }
                }
            });
            bulkQueue = [];
            bulkFileInput.value = '';
            selectedBulkLogoFile = null;
            
            if (bulkLogoPreview) {
                bulkLogoPreviewImg.src = '';
                bulkLogoInput.value = '';
                bulkLogoUploadContent.style.display = 'flex';
                bulkLogoPreview.style.display = 'none';
            }

            updateBulkStats();
            renderBulkQueue();
            showToast('🧹 Antrean dibersihkan!', false);
        });
    }

    // === Init ===
    initAuth();
    updateGenerateBtn();
    updateConvertBtn();

})();

