/**
 * Global state manager - holds last run data, emits change events.
 */

import * as vscode from 'vscode';
import * as fs from 'fs';
import * as fsp from 'fs/promises';
import * as path from 'path';
import type {
  RedconState,
  RunReport,
  AgentPlanReport,
  SimulationReport,
  DoctorReport,
  BenchmarkReport,
  RunHistoryEntry,
} from './types';

class StateManager {
  private _state: RedconState = {
    lastRun: null,
    lastPlan: null,
    lastSimulation: null,
    lastDoctor: null,
    lastBenchmark: null,
    lastTask: '',
    isRunning: false,
    runHistory: [],
  };

  private readonly _onDidChange = new vscode.EventEmitter<keyof RedconState>();
  readonly onDidChange = this._onDidChange.event;

  get state(): Readonly<RedconState> {
    return this._state;
  }

  setRun(run: RunReport): void {
    this._state.lastRun = run;
    this._state.lastTask = run.task;
    this._onDidChange.fire('lastRun');
  }

  setPlan(plan: AgentPlanReport): void {
    this._state.lastPlan = plan;
    this._state.lastTask = plan.task;
    this._onDidChange.fire('lastPlan');
  }

  setSimulation(sim: SimulationReport): void {
    this._state.lastSimulation = sim;
    this._onDidChange.fire('lastSimulation');
  }

  setDoctor(doc: DoctorReport): void {
    this._state.lastDoctor = doc;
    this._onDidChange.fire('lastDoctor');
  }

  setBenchmark(bench: BenchmarkReport): void {
    this._state.lastBenchmark = bench;
    this._onDidChange.fire('lastBenchmark');
  }

  setRunning(running: boolean): void {
    this._state.isRunning = running;
    this._onDidChange.fire('isRunning');
  }

  setHistory(entries: RunHistoryEntry[]): void {
    this._state.runHistory = entries;
    this._onDidChange.fire('runHistory');
  }

  clearHistory(): void {
    this._state.runHistory = [];
    this._onDidChange.fire('runHistory');
  }

  /**
   * Scan workspace for run*.json artifacts and populate history.
   */
  async loadHistory(workspaceRoot: string): Promise<void> {
    const entries: RunHistoryEntry[] = [];
    const redconDir = path.join(workspaceRoot, '.redcon');
    const searchDirs = [workspaceRoot, redconDir];

    for (const dir of searchDirs) {
      if (!fs.existsSync(dir)) {
        continue;
      }

      let files: string[];
      try {
        files = await fsp.readdir(dir);
      } catch {
        continue;
      }

      for (const file of files) {
        if (!file.endsWith('.json')) {
          continue;
        }
        const filePath = path.join(dir, file);
        try {
          const raw = await fsp.readFile(filePath, 'utf-8');
          const data = JSON.parse(raw);
          if (data.command === 'pack' && data.task && data.budget) {
            entries.push({
              path: filePath,
              task: data.task,
              tokens: data.budget.estimated_input_tokens ?? 0,
              maxTokens: data.max_tokens ?? 30000,
              filesIncluded: data.files_included?.length ?? 0,
              generatedAt: data.generated_at ?? '',
              risk: data.budget.quality_risk_estimate ?? 'unknown',
            });
          }
        } catch (e) {
          console.warn('Redcon: skipping invalid artifact:', file, e);
          continue;
        }
      }
    }

    entries.sort(
      (a, b) =>
        new Date(b.generatedAt).getTime() - new Date(a.generatedAt).getTime(),
    );
    entries.splice(50);

    this.setHistory(entries);
  }

  dispose(): void {
    this._onDidChange.dispose();
  }
}

export const state = new StateManager();
