use solverforge::prelude::*;
use solverforge::stream::ConstraintFactory;
use solverforge::SolverConfig;
use super::{Route, Visit};

#[planning_solution(constraints = "constraints", config = "configure")]
pub struct Plan {
    #[planning_entity_collection]
    pub routes: Vec<Route>,
    #[problem_fact_collection]
    pub visits: Vec<Visit>,
    #[planning_score]
    pub score: Option<HardSoftScore>,
    pub seconds: u64,
}

#[solverforge_constraints]
fn constraints() -> impl ConstraintSet<Plan, HardSoftScore> {
    (
        ConstraintFactory::<Plan, HardSoftScore>::new()
            .for_each(Plan::routes())
            .penalize(|r: &Route| HardSoftScore::of_hard(r.violations()))
            .named("required route constraints"),
        ConstraintFactory::<Plan, HardSoftScore>::new()
            .for_each(Plan::routes())
            .penalize(|r: &Route| HardSoftScore::of_soft(r.cost()))
            .named("directed travel seconds"),
    )
}

fn configure(plan: &Plan, _base: SolverConfig) -> SolverConfig {
    SolverConfig::from_toml_str(&format!(r#"
random_seed = 42
[termination]
seconds_spent_limit = {}
[[phases]]
type = "local_search"
[phases.acceptor]
type = "late_acceptance"
late_acceptance_size = 400
"#, plan.seconds)).expect("static solver configuration")
}
