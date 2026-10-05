// SPDX-License-Identifier: AGPL-3.0-only OR LicenseRef-Commercial
//! Once per iTerm2 process: reserve the previous source before collecting new state.
use crate::recovery_store::{Job, Startup, Store};

impl Store {
    pub fn normal_exit(&mut self, epoch: &str) -> Result<(), String> {
        self.writable()?;
        if epoch.is_empty() || epoch != self.index.epoch {
            return Err("Application exit belongs to a different iTerm2 process".into());
        }
        if self.index.clean_exit.as_deref() == Some(epoch) { return Ok(()); }
        let mut index = self.index.clone();
        index.clean_exit = Some(epoch.into());
        self.commit_index(index)
    }

    pub fn prepare_startup(&mut self, epoch: String) -> Result<Startup, String> {
        self.writable()?;
        if epoch.is_empty() || epoch.len() > 128 || epoch.chars().any(char::is_control) {
            return Err("Invalid iTerm2 startup identity".into());
        }
        if let Some(previous) = &self.index.startup {
            if previous.epoch == epoch { return Ok(previous.clone()); }
        }
        let snapshot = if self.index.epoch != epoch {
            self.index.entries.last().map(|e| e.id.clone())
        } else { None };
        let normal_relaunch = self.index.clean_exit.as_deref() == Some(&self.index.epoch) &&
            self.index.epoch != epoch && self.index.epoch.split(':').next() == epoch.split(':').next();
        let nonempty = self.index.enabled && !normal_relaunch && snapshot.as_ref().map(|id| self.restorable(id)).transpose()?.is_some_and(|s| !s.windows.is_empty());
        let startup = Startup { epoch: epoch.clone(), snapshot,
            phase: if self.index.enabled && nonempty { "pending" } else { "done" }.into(), skipped: normal_relaunch };
        let mut index = self.index.clone();
        // A skipped startup must not leave capture held by a dead process's actor.
        // Commit the interruption first: an index failure can safely retry this step.
        if !startup.pending() {
            if let Some(mut job) = self.job.clone().filter(|j| j.status == "running" && j.epoch.as_deref() != Some(&epoch)) {
                job.status = "interrupted".into();
                self.job(job)?;
            }
        }
        index.epoch = epoch;
        index.clean_exit = None;
        index.startup = Some(startup.clone());
        self.commit_index(index)?;
        Ok(startup)
    }

    pub fn begin_startup(&mut self, epoch: &str) -> Result<Option<Job>, String> {
        self.writable()?;
        let startup = self.index.startup.clone().filter(|s| s.epoch == epoch)
            .ok_or("Automatic recovery identity changed")?;
        if !startup.pending() { return Ok(None); }
        if !self.index.enabled {
            let mut index = self.index.clone();
            index.startup.as_mut().unwrap().phase = "done".into();
            self.commit_index(index)?;
            return Ok(None);
        }
        let selected = startup.snapshot.ok_or("Automatic recovery source unavailable")?;
        self.snapshot(&selected)?;
        // A new iTerm2 process cannot have the previous process's recovery actor.
        // The reservation and old journal stay durable until the replacement commits.
        let mut job = self.job.clone().filter(|j| j.snapshot == selected).unwrap_or_else(|| Job {
            id: selected.clone(), snapshot: selected, epoch: None, status: "running".into(), steps: Default::default(),
        });
        job.status = "running".into();
        job.epoch = Some(epoch.into());
        self.job(job.clone())?;
        Ok(Some(job))
    }

    pub fn finish_startup(&mut self, epoch: &str) -> Result<(), String> {
        self.writable()?;
        let startup = self.index.startup.as_ref().filter(|s| s.epoch == epoch)
            .ok_or("Automatic recovery identity changed")?;
        if !startup.pending() { return Ok(()); }
        if !self.job.as_ref().is_some_and(|j| Some(&j.snapshot) == startup.snapshot.as_ref() &&
            j.epoch.as_deref() == Some(epoch) && j.status == "complete") {
            return Err("Automatic recovery incomplete; source preserved and capture paused".into());
        }
        let mut index = self.index.clone();
        index.startup.as_mut().unwrap().phase = "done".into();
        self.commit_index(index)
    }
}
