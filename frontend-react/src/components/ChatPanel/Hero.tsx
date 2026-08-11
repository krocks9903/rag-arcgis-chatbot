import logo from "../../assets/logo.png";
import heroBg from "../../assets/hero-bg.jpg";

export default function Hero() {
  return (
    <div id="hero">
      <div id="hero-bg" style={{ backgroundImage: `url(${heroBg})` }} />
      <div id="hero-content">
        <img id="hero-logo" src={logo} alt="Engage Estero" />
        <h2>Understand Estero development.</h2>
        <p>Search for projects, roads, neighborhoods, votes, and dates across Village records and local news.</p>
      </div>
    </div>
  );
}
