import logo from "../../assets/logo.png";
import heroBg from "../../assets/hero-bg.jpg";

export default function Hero() {
  return (
    <div id="hero">
      <div id="hero-bg" style={{ backgroundImage: `url(${heroBg})` }} />
      <div id="hero-content">
        <img id="hero-logo" src={logo} alt="Engage Estero" />
        <h2>Understand Estero's decisions.</h2>
        <p>Ask anything about Planning, Zoning &amp; Design Board meetings — projects, votes, locations, and dates.</p>
      </div>
    </div>
  );
}
