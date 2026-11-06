solverforge::planning_model! {
    root = "src/domain";
    mod plan;
    mod route;
    mod visit;
    pub use plan::Plan;
    pub use route::Route;
    pub use visit::Visit;
}
