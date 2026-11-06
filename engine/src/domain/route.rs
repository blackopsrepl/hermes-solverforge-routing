use solverforge::prelude::*;
use std::sync::Arc;
use crate::Problem;

#[planning_entity]
pub struct Route {
    #[planning_id]
    pub id: usize,
    #[planning_list_variable(element_collection = "visits")]
    pub visits: Vec<usize>,
    pub problem: Arc<Problem>,
}

impl Route {
    pub fn order(&self) -> Vec<usize> {
        let p = &self.problem;
        let mut order = vec![p.input.origin];
        order.extend(self.visits.iter().map(|&i| p.mandatory[i]));
        if let Some(end) = p.endpoint() {
            if order.len() > 1 || end != p.input.origin {
                order.push(end);
            }
        }
        order
    }

    pub fn cost(&self) -> i64 {
        self.order().windows(2).filter_map(|w| self.problem.input.matrix[w[0]][w[1]]).sum()
    }

    pub fn violations(&self) -> i64 {
        let p = &self.problem;
        let order = self.order();
        let mut bad = p.mandatory.len().abs_diff(self.visits.len()) as i64;
        bad += order.windows(2).filter(|w| p.input.matrix[w[0]][w[1]].is_none()).count() as i64;
        if p.input.fixed_first.is_some_and(|first| order.get(1) != Some(&first)) {
            bad += 1;
        }
        for &[a, b] in &p.input.precedence {
            match (order.iter().position(|&x| x == a), order.iter().position(|&x| x == b)) {
                (Some(a), Some(b)) if a < b => {},
                _ => bad += 1,
            }
        }
        bad
    }
}
