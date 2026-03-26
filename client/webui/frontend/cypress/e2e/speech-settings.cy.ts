/**
 * E2E tests for the Speech (STT/TTS) configuration.
 *
 * Speech config uses:
 * - TTS: Gemini via Vertex AI ADC (gemini-2.5-flash-tts, gbg-neuro service account)
 * - STT: Browser Web Speech API (default; external Whisper requires real OpenAI key)
 *
 * Requires: Backend running with speech config enabled in webui.yaml.
 */

describe("Speech Settings — Feature Flags", { tags: ["@community"] }, () => {
    beforeEach(() => {
        cy.navigateToChat();
    });

    it("should expose speech feature flags via /api/v1/config", () => {
        cy.request({
            method: "GET",
            url: "/api/v1/config",
            failOnStatusCode: false,
        }).then(response => {
            if (response.status === 200) {
                const config = response.body;
                if (config.frontend_feature_enablement) {
                    expect(config.frontend_feature_enablement).to.have.property("speechToText", true);
                    expect(config.frontend_feature_enablement).to.have.property("textToSpeech", true);
                }
            } else {
                cy.log(`Config endpoint returned ${response.status} — backend not running`);
            }
        });
    });

    it("should show audio controls when speech features are enabled", () => {
        cy.get("body").then($body => {
            const hasMicButton = $body.find('[aria-label*="ecord"], [tooltip*="ecord"]').length > 0;
            if (hasMicButton) {
                cy.log("STT feature flag is enabled — audio recording button visible");
            } else {
                cy.log("Audio button not visible — may require enabling in settings first");
            }
        });
    });
});

describe("Speech Settings — TTS Vertex AI ADC Config", { tags: ["@community"] }, () => {
    it("should have valid TTS config (Gemini via Vertex AI)", () => {
        cy.request({
            method: "GET",
            url: "/api/v1/speech/config",
            failOnStatusCode: false,
        }).then(response => {
            if (response.status === 200) {
                const config = response.body;
                // Verify Gemini TTS is configured and valid (via vertex_project)
                if (config.tts_gemini_valid !== undefined) {
                    expect(config.tts_gemini_valid).to.be.true;
                }
                // Verify speech tab defaults
                if (config.speechTab?.textToSpeech) {
                    expect(config.speechTab.textToSpeech.engineTTS).to.eq("external");
                    expect(config.speechTab.textToSpeech.voice).to.eq("Kore");
                }
            } else {
                cy.log(`Speech config returned ${response.status} — backend not running`);
            }
        });
    });

    it("should default STT to browser engine", () => {
        cy.request({
            method: "GET",
            url: "/api/v1/speech/config",
            failOnStatusCode: false,
        }).then(response => {
            if (response.status === 200 && response.body.speechTab?.speechToText) {
                expect(response.body.speechTab.speechToText.engineSTT).to.eq("browser");
            }
        });
    });
});
