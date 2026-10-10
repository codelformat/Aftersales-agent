import { formatDeepLink } from '../theater/deepLink';
import { FACTS, LINKS } from './facts';
import styles from './Welcome.module.css';

export default function WelcomePage() {
  return <section lang="en" aria-labelledby="welcome-title" className={styles.welcome}>
    <header className={styles.hero}>
      <p className={styles.eyebrow}>Agent engineering project · Sheng (Harry) Guan</p>
      <h1 id="welcome-title">An after-sales support agent that knows when not to answer.</h1>
      <p className={styles.intro}>It routes each question, retrieves evidence, and cites it. When the evidence is weak, it refuses and sends the question to human review. Every screen on this site replays a real recorded session.</p>
      <ul aria-label="Proof points" className={styles.proof}>
        <li><a href={LINKS.actions} target="_blank" rel="noreferrer">{FACTS.backendTests} backend tests in CI</a></li>
        <li><a href="#/theater">{FACTS.scenes} recorded real sessions</a></li>
        <li><a href={`${LINKS.repo}#measured-not-claimed`} target="_blank" rel="noreferrer">Recall@1 {FACTS.recallAt1} · faithfulness {FACTS.faithfulness}</a></li>
      </ul>
      <div className={styles.actions}>
        <a className={styles.primary} href={formatDeepLink({ scene: 'flywheel', seconds: 0, view: 'eng', lang: 'en', autoplay: true })}>Watch the knowledge flywheel (27 s)</a>
        <a href="#/theater">Browse all {FACTS.scenes} scenes</a>
      </div>
    </header>
    <section className={styles.overview} aria-labelledby="welcome-overview">
      <h2 id="welcome-overview">What you are looking at</h2>
      <ul className={styles.features}>
        <li><h3>A glass-box agent</h3><p>Switch to the engineering view to see each graph node, the retrieved evidence, the confidence gate and token use.</p></li>
        <li><h3>A gate that can say no</h3><p>A calibrated score decides whether the evidence is strong enough. Weak evidence gets a safe reply, not a guess.</p></li>
        <li><h3>Humans close the loop</h3><p>Refused questions go to a review queue. An approved answer is indexed and used in the next reply.</p></li>
      </ul>
    </section>
    <section className={styles.builder} aria-labelledby="welcome-builder">
      <h2 id="welcome-builder">About the builder</h2>
      <p className={styles.identity}>Sheng (Harry) Guan · MPhil CSE, CUHK</p>
      <p>Open to LLM and agent engineering roles in Hong Kong and mainland China.</p>
      <div className={styles.actions}>
        <a href={LINKS.portfolio} target="_blank" rel="noreferrer">Portfolio</a>
        <a href={LINKS.repo} target="_blank" rel="noreferrer">Source code</a>
        <a href={LINKS.linkedin} target="_blank" rel="noreferrer">LinkedIn</a>
      </div>
    </section>
    <footer className={styles.footer}>Recorded on 2026-10-10 with deepseek-v4-flash. <a href={LINKS.runLocally} target="_blank" rel="noreferrer">Run the full system locally</a></footer>
  </section>;
}
