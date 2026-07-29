# Frontend

The React client uploads one image, observes the Direct run, previews returned
GLSL, and shows the selected attempt plus safe retry failures.

The live timeline renders the two parent lifecycle stages and all 16 nodes from
the LayerPlan Direct attempt graph. Node events contain only a stable node name,
lifecycle status, attempt index, engine id, and optional duration; private graph
state, reference bytes, shader source, and raw exceptions never enter the
frontend progress contract.

```bash
make dev-frontend
npm --prefix frontend run test
npm --prefix frontend run build
```
