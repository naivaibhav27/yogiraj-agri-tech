let recognition = null;

function initializeSpeechEngine() {
    if (!('webkitSpeechRecognition' in window) && !('SpeechRecognition' in window)) {
        alert("Speech recognition is not supported in this browser. Please use Google Chrome.");
        return false;
    }
    
    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
    recognition = new SpeechRecognition();
    recognition.continuous = false;
    recognition.interimResults = false;
    recognition.lang = 'gu-IN'; // Default to Gujarati (Supports 'hi-IN' and 'en-IN')

    recognition.onstart = function() {
        const statusEl = document.getElementById('voiceStatus');
        if (statusEl) {
            statusEl.className = 'badge bg-danger';
            statusEl.innerText = 'Listening...';
        }
    };

    recognition.onresult = function(event) {
        const transcript = event.results[0][0].transcript;
        const transcriptEl = document.getElementById('transcriptOutput');
        if (transcriptEl) {
            transcriptEl.innerText = `"${transcript}"`;
        }

        const statusEl = document.getElementById('voiceStatus');
        if (statusEl) {
            statusEl.className = 'badge bg-success';
            statusEl.innerText = 'Processed';
        }

        // Voice Response Feedback
        speakResponse("તમારો પ્રશ્ન સમજાઈ ગયો છે. આસિસ્ટન્ટ પ્રોસેસ કરી રહ્યો છે.");
    };

    recognition.onerror = function(event) {
        const statusEl = document.getElementById('voiceStatus');
        if (statusEl) {
            statusEl.className = 'badge bg-warning text-dark';
            statusEl.innerText = 'Error or Timeout';
        }
    };
    return true;
}

function startListening() {
    if (!recognition) {
        if (!initializeSpeechEngine()) return;
    }
    recognition.start();
}

function speakResponse(text) {
    if ('speechSynthesis' in window) {
        const synth = window.speechSynthesis;
        const utterance = new SpeechSynthesisUtterance(text);
        utterance.lang = 'gu-IN';
        synth.speak(utterance);
    }
}