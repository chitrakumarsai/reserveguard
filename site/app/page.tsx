import { Footer } from "@/components/Footer";
import { Nav } from "@/components/Nav";
import { ConsoleFrame } from "@/components/sections/ConsoleFrame";
import { Features } from "@/components/sections/Features";
import { Hero } from "@/components/sections/Hero";
import { Integrations } from "@/components/sections/Integrations";
import { Questions } from "@/components/sections/Questions";

// Plan (screens at 1440x900): hero 1 · console 1 · rates 2 · integrations 1 · questions 1 · closing 1.
export default function Home() {
  return (
    <>
      <Nav />
      <main id="top">
        <Hero />
        <ConsoleFrame />
        <Features />
        <Integrations />
        <Questions />
      </main>
      <Footer />
    </>
  );
}
