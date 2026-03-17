"use client";

import { useState } from "react";

const STEPS = [
  {
    title: "Co to jest Redcon?",
    icon: (
      <svg className="w-8 h-8 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
      </svg>
    ),
    content: (
      <div className="space-y-3 text-sm text-white/60">
        <p>
          Agenty AI (np. Cursor, Copilot, Claude) wysylaja <strong className="text-white">cale repozytorium</strong> do modelu przy kazdym wywolaniu.
          To kosztuje duzo tokenow i sprawia, ze model dostaje mnstwo szumu.
        </p>
        <p>
          <strong className="text-white">Redcon</strong> analizuje zadanie i wybiera tylko te pliki i fragmenty kodu,
          ktore sa naprawde istotne - kompresujac kontekst nawet o <strong className="text-emerald-400">90%+</strong>.
        </p>
        <div className="bg-white/5 border border-white/10 rounded-lg p-3 font-mono text-xs space-y-1">
          <div className="flex gap-3">
            <span className="text-red-400 w-24 flex-shrink-0">Bez Redcon</span>
            <span className="text-white/40">15 plikow - 12 228 tokenow - kazde wywolanie</span>
          </div>
          <div className="flex gap-3">
            <span className="text-emerald-400 w-24 flex-shrink-0">Z Redcon</span>
            <span className="text-white/40">4-8 plikow - 633 tokeny - tylko co potrzebne</span>
          </div>
        </div>
      </div>
    ),
  },
  {
    title: "Jak opisac zadanie",
    icon: (
      <svg className="w-8 h-8 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M11 5H6a2 2 0 00-2 2v11a2 2 0 002 2h11a2 2 0 002-2v-5m-1.414-9.414a2 2 0 112.828 2.828L11.828 15H9v-2.828l8.586-8.586z" />
      </svg>
    ),
    content: (
      <div className="space-y-3 text-sm text-white/60">
        <p>
          W pole <strong className="text-white">Task</strong> wpisz co chcesz zrobic - tak jak opisujesz zadanie agentowi.
          Im bardziej konkretny opis, tym lepiej Redcon dobierze pliki.
        </p>
        <div className="space-y-1.5">
          <p className="text-xs font-semibold text-white/30 uppercase tracking-wide">Przyklady dobrych zadan:</p>
          {[
            "Add rate limiting middleware to API endpoints",
            "Refactor the database connection module",
            "Add JWT authentication to user routes",
          ].map((t) => (
            <div key={t} className="bg-accent/10 border border-accent/20 rounded px-3 py-1.5 text-xs font-mono text-accent-light">
              {t}
            </div>
          ))}
        </div>
        <p className="text-xs text-white/30">
          Mozesz tez kliknac jeden z gotowych przykladow pod polem - od razu uruchomi demo.
        </p>
      </div>
    ),
  },
  {
    title: "Scoring - ocenianie plikow",
    icon: (
      <svg className="w-8 h-8 text-accent" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
      </svg>
    ),
    content: (
      <div className="space-y-3 text-sm text-white/60">
        <p>Redcon ocenia kazdy plik w repo i nadaje mu <strong className="text-white">score</strong>. Score laczy kilka sygnalow:</p>
        <div className="space-y-2">
          {[
            { label: "Semantyczny", desc: "Czy nazwa pliku / symbole pasuja do slow kluczowych zadania?" },
            { label: "Strukturalny", desc: "Graf importow - czy plik jest blisko punktu wejscia?" },
            { label: "Historyczny", desc: "Czy plik byl wybierany w podobnych zadaniach wczesniej?" },
          ].map(({ label, desc }) => (
            <div key={label} className="flex gap-3">
              <span className="w-24 flex-shrink-0 text-xs font-semibold text-accent pt-0.5">{label}</span>
              <span className="text-xs text-white/50">{desc}</span>
            </div>
          ))}
        </div>
        <p className="text-xs text-white/30">Pliki z najwyzszym score trafiaja do kontekstu. Reszta jest pomijana.</p>
      </div>
    ),
  },
  {
    title: "Redukcja tokenow",
    icon: (
      <svg className="w-8 h-8 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
      </svg>
    ),
    content: (
      <div className="space-y-3 text-sm text-white/60">
        <p>Po kliknieciu <strong className="text-white">Run</strong> zobaczysz pasek redukcji tokenow:</p>
        <div className="bg-white/5 border border-white/10 rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2">
            <span className="text-xs w-16 text-white/40">Baseline</span>
            <div className="flex-1 bg-white/10 rounded-full h-2.5">
              <div className="bg-white/30 h-2.5 rounded-full w-full" />
            </div>
            <span className="text-xs text-white/40 w-20 text-right">~12 228 tok</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs w-16 text-white/40">Redcon</span>
            <div className="flex-1 bg-white/10 rounded-full h-2.5">
              <div className="bg-accent h-2.5 rounded-full w-[5%]" />
            </div>
            <span className="text-xs text-accent-light font-semibold w-20 text-right">~633 tok</span>
          </div>
        </div>
        <p>
          <strong className="text-emerald-400">-94%</strong> mniej tokenow oznacza mniejszy koszt wywolania i szybsza odpowiedz modelu.
          Wskaznik <em>quality risk</em> informuje czy kompresja nie pomija czegos waznego.
        </p>
      </div>
    ),
  },
  {
    title: "Strategie kompresji",
    icon: (
      <svg className="w-8 h-8 text-violet-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 4-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4m0 5c0 2.21-3.582 4-8 4s-8-1.79-8-4" />
      </svg>
    ),
    content: (
      <div className="space-y-3 text-sm text-white/60">
        <p>Kazdy plik dostaje inna strategie zalezna od jego rozmiaru i istotnosci:</p>
        <div className="space-y-2">
          {[
            { badge: "symbol extract", color: "bg-accent/20 text-accent-light", desc: "Wyciaga tylko klasy i funkcje istotne dla zadania. Pomija boilerplate i docstringi." },
            { badge: "snippet", color: "bg-amber-900/40 text-amber-300", desc: "Wybiera najistotniejszy fragment pliku wg oceny linii." },
            { badge: "summary", color: "bg-violet-900/40 text-violet-300", desc: "Deterministyczne podsumowanie struktury pliku - stubsy metod bez cial." },
            { badge: "full file", color: "bg-white/10 text-white/60", desc: "Maly plik - nie warto kompresowac, wysylany w calosci." },
          ].map(({ badge, color, desc }) => (
            <div key={badge} className="flex items-start gap-3">
              <span className={`mt-0.5 flex-shrink-0 inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${color}`}>{badge}</span>
              <span className="text-xs text-white/50">{desc}</span>
            </div>
          ))}
        </div>
        <p className="text-xs text-white/30">Kliknij dowolny plik na liscie aby zobaczyc dokladnie co trafi do modelu.</p>
      </div>
    ),
  },
  {
    title: "Gotowe - uruchom demo!",
    icon: (
      <svg className="w-8 h-8 text-emerald-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
        <path strokeLinecap="round" strokeLinejoin="round" d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
      </svg>
    ),
    content: (
      <div className="space-y-3 text-sm text-white/60">
        <p>Teraz juz wiesz jak dziala Redcon. Wyprobuj go:</p>
        <div className="space-y-2">
          {[
            "1. Wpisz zadanie lub kliknij przyklad",
            "2. Kliknij Run i poczekaj chwile",
            "3. Sprawdz ile tokenow zaoszczedzil Redcon",
            "4. Kliknij pliki aby zobaczyc skompresowany kontekst",
          ].map((s) => (
            <div key={s} className="flex gap-2 text-sm">
              <span className="text-accent font-mono text-xs pt-0.5">&gt;</span>
              <span>{s}</span>
            </div>
          ))}
        </div>
        <p className="text-xs text-white/30 pt-1">
          Demo uzywa prawdziwego repozytorium Python FastAPI z 15 plikami (~12k tokenow baseline).
        </p>
      </div>
    ),
  },
];

export default function DemoTour({ onClose }: { onClose: () => void }) {
  const [step, setStep] = useState(0);
  const current = STEPS[step];
  const isLast = step === STEPS.length - 1;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 backdrop-blur-sm">
      <div className="bg-card border border-white/10 rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
        {/* Progress bar */}
        <div className="h-0.5 bg-white/5">
          <div
            className="h-0.5 bg-accent transition-all duration-300"
            style={{ width: `${((step + 1) / STEPS.length) * 100}%` }}
          />
        </div>

        {/* Header */}
        <div className="flex items-start gap-4 px-6 pt-6 pb-4">
          <div className="flex-shrink-0">{current.icon}</div>
          <div className="flex-1 min-w-0">
            <div className="text-xs font-semibold text-white/30 uppercase tracking-wide mb-1">
              Krok {step + 1} z {STEPS.length}
            </div>
            <h2 className="text-lg font-bold text-white leading-tight">{current.title}</h2>
          </div>
          <button onClick={onClose} className="flex-shrink-0 text-white/30 hover:text-white/60 p-1">
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </div>

        {/* Content */}
        <div className="px-6 pb-6">{current.content}</div>

        {/* Footer */}
        <div className="flex items-center justify-between px-6 py-4 bg-white/5 border-t border-white/5">
          <button
            onClick={() => setStep((s) => s - 1)}
            disabled={step === 0}
            className="text-sm text-white/40 hover:text-white/70 disabled:opacity-0 disabled:pointer-events-none px-3 py-1.5"
          >
            Wstecz
          </button>

          <div className="flex gap-1.5">
            {STEPS.map((_, i) => (
              <button
                key={i}
                onClick={() => setStep(i)}
                className={`w-2 h-2 rounded-full transition-colors ${i === step ? "bg-accent" : "bg-white/20 hover:bg-white/40"}`}
              />
            ))}
          </div>

          {isLast ? (
            <button
              onClick={onClose}
              className="text-sm font-semibold px-4 py-1.5 bg-accent text-white rounded-lg hover:bg-accent-dark"
            >
              Zacznij
            </button>
          ) : (
            <button
              onClick={() => setStep((s) => s + 1)}
              className="text-sm font-semibold px-4 py-1.5 bg-accent text-white rounded-lg hover:bg-accent-dark"
            >
              Dalej
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
